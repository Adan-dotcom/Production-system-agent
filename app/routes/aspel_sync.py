"""
POST /aspel/sync              — upload Excel from Aspel manually
POST /aspel/sync-from-path    — read Excel from ASPEL_EXCEL_PATH in .env
GET  /aspel/path-status       — check whether ASPEL_EXCEL_PATH is configured & readable
GET  /aspel/imports           — list sync history
GET  /aspel/imports/{id}/rows — rows with errors for a specific sync
"""

import os
import threading
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.db import get_db
from app import models
from app.auth import get_current_user, require_admin
from app.services.aspel_parser import parse_aspel_excel
from app.scheduler import get_auto_sync_status, notify_manual_sync

# Use absolute path to .env relative to this file's location (project root)
_DOTENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=_DOTENV_PATH, override=True)

# Prevents concurrent syncs (scheduler thread + manual endpoint request)
_sync_lock = threading.Lock()


def _get_aspel_path() -> str:
    """
    Read ASPEL_EXCEL_PATH directly from the .env file on every call.
    This bypasses any os.environ caching and works reliably even when
    the env var is inherited as an empty string from the parent shell.
    """
    # 1) Try reading from .env file directly (most reliable)
    if _DOTENV_PATH.exists():
        for raw_line in _DOTENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() == "ASPEL_EXCEL_PATH":
                return val.strip()
    # 2) Fall back to os.environ (in case the var was set via real system env)
    return os.getenv("ASPEL_EXCEL_PATH", "").strip()


router = APIRouter()


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _get_or_create_legacy_refs(db: Session):
    """Ensure at least one Cliente and Producto row exist for FK compat."""
    default_customer = db.query(models.Cliente).first()
    if not default_customer:
        default_customer = models.Cliente(name="(Sin cliente)")
        db.add(default_customer)
        db.commit()
        db.refresh(default_customer)

    default_product = db.query(models.Producto).first()
    if not default_product:
        default_product = models.Producto(
            description="(Producto legacy)", tare_constant_kg=0, tolerance_percent=5
        )
        db.add(default_product)
        db.commit()
        db.refresh(default_product)

    return default_customer, default_product


def _do_sync(
    db: Session,
    file_bytes: bytes,
    filename: str,
    source: str,            # "upload_manual" | "configured_path"
    triggered_by_id: int,
) -> dict:
    """
    Core sync logic shared by manual upload and path-based sync.
    Parses the Excel, upserts orders/lines, logs everything.
    Returns the summary dict sent back to the caller.
    """
    if not _sync_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Ya hay una sincronización en curso. Espera unos segundos.")
    try:
        return _do_sync_inner(db, file_bytes, filename, source, triggered_by_id)
    finally:
        _sync_lock.release()


def _do_sync_inner(
    db: Session,
    file_bytes: bytes,
    filename: str,
    source: str,
    triggered_by_id: int,
) -> dict:
    # Create import log
    imp = models.AspelExcelImport(
        filename=filename,
        source=source,
        triggered_by=triggered_by_id,
        status="running",
    )
    db.add(imp)
    db.commit()
    db.refresh(imp)

    try:
        parsed = parse_aspel_excel(file_bytes)
    except ValueError as e:
        imp.status = "error"
        imp.finished_at = datetime.now()
        imp.error_summary = str(e)
        db.commit()
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        imp.status = "error"
        imp.finished_at = datetime.now()
        imp.error_summary = f"Error inesperado al leer el Excel: {e}"
        db.commit()
        raise HTTPException(status_code=500, detail=f"Error al procesar el Excel: {e}")

    default_customer, default_product = _get_or_create_legacy_refs(db)

    imported_orders = 0
    updated_orders  = 0
    imported_lines  = 0
    updated_lines   = 0
    failed_rows     = 0

    # ── Save raw rows for audit ────────────────────────────────────────────
    for rr in parsed["raw_rows"]:
        raw_row = models.AspelExcelRawRow(
            import_id         = imp.id,
            row_number        = rr["row_number"],
            raw_data          = rr["raw"],         # dict → JSONB directly
            normalized_data   = rr["normalized"],  # dict → JSONB directly
            validation_status = rr["status"],
            validation_errors = rr["errors"] or None,
        )
        db.add(raw_row)
        if rr["status"] in ("error", "skipped"):
            failed_rows += 1
    db.commit()

    # ── Upsert orders and lines ────────────────────────────────────────────
    for sae_num, order_data in parsed["orders"].items():
        existing_order = db.query(models.SalesOrder).filter(
            models.SalesOrder.sae_order_number == sae_num
        ).first()

        # Resolve or create cliente record
        customer_name = order_data.get("customer_name") or ""
        cliente = None
        if customer_name:
            cliente = db.query(models.Cliente).filter(
                models.Cliente.name == customer_name
            ).first()
            if not cliente:
                cliente = models.Cliente(name=customer_name)
                db.add(cliente)
                db.commit()
                db.refresh(cliente)
        if not cliente:
            cliente = default_customer

        if existing_order:
            existing_order.customer_code    = order_data.get("customer_code") or existing_order.customer_code
            existing_order.seller_code      = order_data.get("seller_code")   or existing_order.seller_code
            existing_order.seller_name      = order_data.get("seller_name")   or existing_order.seller_name
            existing_order.order_status     = order_data.get("order_status")  or existing_order.order_status
            existing_order.shipping_address = order_data.get("shipping_address") or existing_order.shipping_address
            existing_order.raw_source       = "aspel_excel"
            existing_order.updated_at       = datetime.now()
            if order_data.get("order_date"):
                existing_order.order_date = order_data["order_date"]
            if order_data.get("delivery_date"):
                existing_order.delivery_date = order_data["delivery_date"]
            if order_data.get("target_kg") is not None:
                existing_order.target_kg = order_data["target_kg"]
            if cliente and cliente.customer_id != existing_order.customer_id:
                existing_order.customer_id = cliente.customer_id
            db.commit()
            so = existing_order
            updated_orders += 1
        else:
            so = models.SalesOrder(
                sae_order_number = sae_num,
                customer_id      = cliente.customer_id,
                product_id       = default_product.product_id,
                ordered_kg       = order_data.get("target_kg") or 0,
                customer_code    = order_data.get("customer_code"),
                seller_code      = order_data.get("seller_code"),
                seller_name      = order_data.get("seller_name"),
                order_status     = order_data.get("order_status"),
                order_date       = order_data.get("order_date"),
                delivery_date    = order_data.get("delivery_date"),
                shipping_address = order_data.get("shipping_address"),
                target_kg        = order_data.get("target_kg"),
                raw_source       = "aspel_excel",
            )
            db.add(so)
            db.commit()
            db.refresh(so)

            # Auto-create production lot
            lot_code = f"AUTO-{sae_num}"
            if not db.query(models.ProductionLot).filter(
                models.ProductionLot.lot_code == lot_code
            ).first():
                db.add(models.ProductionLot(
                    sales_order_id=so.sales_order_id,
                    lot_code=lot_code,
                ))
                db.commit()

            imported_orders += 1

        # ── Upsert lines ───────────────────────────────────────────────────
        existing_line_numbers = {
            ln.line_number
            for ln in db.query(models.SalesOrderLine).filter(
                models.SalesOrderLine.sales_order_id == so.sales_order_id
            ).all()
        }

        for line_data in order_data.get("lines", []):
            product_code = line_data.get("product_code") or ""
            measure      = line_data.get("measure")
            caliber      = line_data.get("caliber")
            descr        = line_data.get("product_description") or product_code or "—"

            # Find by product_code first, then by line_number
            existing_line = None
            if product_code:
                existing_line = db.query(models.SalesOrderLine).filter(
                    models.SalesOrderLine.sales_order_id == so.sales_order_id,
                    models.SalesOrderLine.product_code   == product_code,
                ).first()
            if not existing_line:
                existing_line = db.query(models.SalesOrderLine).filter(
                    models.SalesOrderLine.sales_order_id == so.sales_order_id,
                    models.SalesOrderLine.line_number    == line_data["line_number"],
                ).first()

            target_kg = line_data.get("target_kg")

            if existing_line:
                existing_line.product_code        = product_code or existing_line.product_code
                existing_line.product_description = descr or existing_line.product_description
                existing_line.measure             = measure or existing_line.measure
                existing_line.caliber             = caliber or existing_line.caliber
                if line_data.get("quantity_ordered") is not None:
                    existing_line.quantity_ordered = line_data["quantity_ordered"]
                if target_kg is not None:
                    existing_line.target_kg  = target_kg
                    existing_line.ordered_kg = target_kg
                if line_data.get("unit_price") is not None:
                    existing_line.unit_price = line_data["unit_price"]
                if line_data.get("line_total") is not None:
                    existing_line.line_total = line_data["line_total"]
                existing_line.following_document = line_data.get("following_document") or existing_line.following_document
                existing_line.raw_source = "aspel_excel"
                existing_line.updated_at = datetime.now()
                db.commit()
                updated_lines += 1
            else:
                line_num = line_data["line_number"]
                while line_num in existing_line_numbers:
                    line_num += 1
                existing_line_numbers.add(line_num)

                db.add(models.SalesOrderLine(
                    sales_order_id            = so.sales_order_id,
                    line_number               = line_num,
                    product_code              = product_code,
                    product_description       = descr,
                    measure                   = measure,
                    caliber                   = caliber,
                    quantity_ordered          = line_data.get("quantity_ordered"),
                    target_kg                 = target_kg,
                    ordered_kg                = target_kg or 0,
                    unit_price                = line_data.get("unit_price"),
                    line_total                = line_data.get("line_total"),
                    following_document        = line_data.get("following_document"),
                    completed_kg              = 0,
                    status                    = "open",
                    default_branding_mode     = "normal",
                    default_print_weight_mode = "gross",
                    label_template            = "internal",
                    raw_source                = "aspel_excel",
                ))
                db.commit()
                imported_lines += 1

    # ── Finalise import log ────────────────────────────────────────────────
    imp.status          = "done"
    imp.finished_at     = datetime.now()
    imp.total_rows      = parsed["total_rows"]
    imp.imported_orders = imported_orders
    imp.updated_orders  = updated_orders
    imp.imported_lines  = imported_lines
    imp.updated_lines   = updated_lines
    imp.failed_rows     = failed_rows
    db.commit()

    return {
        "message":         "Sincronización completada",
        "import_id":       imp.id,
        "source":          source,
        "filename":        filename,
        "total_rows":      parsed["total_rows"],
        "imported_orders": imported_orders,
        "updated_orders":  updated_orders,
        "imported_lines":  imported_lines,
        "updated_lines":   updated_lines,
        "failed_rows":     failed_rows,
        "status":          "done",
    }


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/sync")
async def sync_aspel_excel(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Upload an Excel file manually and sync orders/lines. Idempotent."""
    if not file.filename.lower().endswith((".xlsx", ".xls", ".xlsm")):
        raise HTTPException(status_code=400, detail="El archivo debe ser .xlsx, .xls o .xlsm")

    file_bytes = await file.read()
    if len(file_bytes) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Archivo demasiado grande (máx 20 MB)")

    result = _do_sync(db, file_bytes, file.filename, "upload_manual", current_user.user_id)
    notify_manual_sync("ok", f"Upload manual: {result['imported_orders']} PI nuevos, {result['updated_orders']} actualizados")
    return result


@router.post("/sync-from-path")
async def sync_aspel_from_path(
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """
    Read the Excel from ASPEL_EXCEL_PATH (set in .env) and sync.
    Intended for production where Aspel exports to a fixed path automatically.
    """
    path_str = _get_aspel_path()
    if not path_str:
        raise HTTPException(
            status_code=422,
            detail=(
                "ASPEL_EXCEL_PATH no está configurada en .env. "
                "Agrega: ASPEL_EXCEL_PATH=C:\\Aspel\\Reportes\\Pedidos.xlsx"
            ),
        )

    p = Path(path_str)
    if not p.exists():
        raise HTTPException(
            status_code=422,
            detail=f"Archivo no encontrado en la ruta configurada: {path_str}",
        )
    if p.suffix.lower() not in (".xlsx", ".xls", ".xlsm"):
        raise HTTPException(
            status_code=422,
            detail=f"La ruta no apunta a un archivo Excel válido: {path_str}",
        )

    try:
        file_bytes = p.read_bytes()
    except PermissionError:
        raise HTTPException(status_code=422, detail=f"Sin permiso de lectura: {path_str}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al leer el archivo: {e}")

    if len(file_bytes) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Archivo demasiado grande (máx 20 MB)")

    result = _do_sync(db, file_bytes, p.name, "configured_path", current_user.user_id)
    notify_manual_sync("ok", f"Desde ruta: {result['imported_orders']} PI nuevos, {result['updated_orders']} actualizados")
    return result


@router.get("/path-status")
def aspel_path_status(
    current_user=Depends(get_current_user),
):
    """
    Returns ASPEL_EXCEL_PATH config status: configured?, file exists?, size, last modified.
    Used by the admin UI to show a status badge before syncing from path.
    """
    path_str = _get_aspel_path()
    if not path_str:
        return {
            "configured": False,
            "path":       None,
            "exists":     False,
            "size_mb":    None,
            "modified":   None,
            "message":    "ASPEL_EXCEL_PATH no configurada en .env",
        }

    p = Path(path_str)
    exists   = p.exists()
    size_mb  = None
    modified = None
    if exists:
        try:
            stat     = p.stat()
            size_mb  = round(stat.st_size / 1024 / 1024, 2)
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%d/%m/%Y %H:%M")
        except Exception:
            pass

    return {
        "configured": True,
        "path":       str(p),
        "exists":     exists,
        "size_mb":    size_mb,
        "modified":   modified,
        "message": (
            f"OK — {p.name}  ({size_mb} MB, modificado {modified})"
            if exists
            else f"Archivo no encontrado: {path_str}"
        ),
    }


# ── Auto-sync status ──────────────────────────────────────────────────────────

@router.get("/auto-sync-status")
def auto_sync_status(current_user=Depends(get_current_user)):
    """Return current state of the background auto-sync scheduler."""
    return get_auto_sync_status()


# ── History endpoints ──────────────────────────────────────────────────────────

@router.get("/imports")
def list_imports(
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List recent Excel sync runs."""
    rows = (
        db.query(models.AspelExcelImport)
        .order_by(models.AspelExcelImport.started_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id":              r.id,
            "filename":        r.filename,
            "source":          r.source or "upload_manual",
            "status":          r.status,
            "started_at":      r.started_at.isoformat() if r.started_at else None,
            "finished_at":     r.finished_at.isoformat() if r.finished_at else None,
            "total_rows":      r.total_rows,
            "imported_orders": r.imported_orders,
            "updated_orders":  r.updated_orders,
            "imported_lines":  r.imported_lines,
            "updated_lines":   r.updated_lines,
            "failed_rows":     r.failed_rows,
            "error_summary":   r.error_summary,
        }
        for r in rows
    ]


@router.get("/imports/{import_id}/rows")
def get_import_rows(
    import_id: int,
    status_filter: str = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get raw/normalized rows for a specific import. Filter: error|warning|ok|skipped."""
    imp = db.query(models.AspelExcelImport).filter(
        models.AspelExcelImport.id == import_id
    ).first()
    if not imp:
        raise HTTPException(status_code=404, detail="Import no encontrado")

    q = db.query(models.AspelExcelRawRow).filter(
        models.AspelExcelRawRow.import_id == import_id
    )
    if status_filter:
        q = q.filter(models.AspelExcelRawRow.validation_status == status_filter)

    rows = q.order_by(models.AspelExcelRawRow.row_number.asc()).limit(500).all()
    return [
        {
            "row_number":        r.row_number,
            "validation_status": r.validation_status,
            "validation_errors": r.validation_errors,
            "normalized_data":   r.normalized_data,  # already a dict (JSONB)
        }
        for r in rows
    ]
