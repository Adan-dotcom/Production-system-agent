from io import BytesIO
from pathlib import Path

from barcode import Code128
from barcode.writer import ImageWriter
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app import models, schemas
from app.auth import get_current_user

router = APIRouter()

BASE_DIR   = Path(__file__).resolve().parent.parent.parent
LABELS_DIR = BASE_DIR / "static" / "labels"
LABELS_DIR.mkdir(parents=True, exist_ok=True)

templates = Jinja2Templates(directory="templates")

# Human-readable stage names for the label title (non-distributor only)
STAGE_LABELS = {
    "extrusion":  "EXTRUSIÓN",
    "corte":      "CORTE",
    "impresion":  "IMPRESIÓN",
    "bolseo":     "BOLSEO",
    "almacen":    "PRODUCTO TERMINADO",
    "reciclaje":  "RECICLAJE",
}


def build_barcode_png(barcode_value: str, filename_key: str) -> str:
    """Generate a Code128 barcode PNG and return its static URL."""
    safe_key  = filename_key.replace("/", "_").replace("\\", "_")
    file_path = LABELS_DIR / f"{safe_key}.png"
    writer    = ImageWriter()
    buf = BytesIO()
    # OJO: estas opciones deben pasarse a .write(), NO a writer.set_options() antes de
    # construir — Code128.render() resetea module_width/quiet_zone a sus mínimos internos
    # (0.2mm / 2.54mm) salvo que se le pasen aquí, así que set_options() antes no tenía efecto.
    Code128(barcode_value, writer=writer).write(
        buf, {"module_width": 0.4, "module_height": 15.0, "quiet_zone": 4.0, "write_text": True}
    )
    buf.seek(0)
    file_path.write_bytes(buf.read())
    return f"/static/labels/{safe_key}.png"


def _resolve_label_context(item, db: Session) -> dict:
    """Gather all data needed to render a label preview."""
    line = order = customer = machine = operator = None

    if item.sales_order_line_id:
        line = db.query(models.SalesOrderLine).filter(
            models.SalesOrderLine.sales_order_line_id == item.sales_order_line_id
        ).first()
        if line:
            order = db.query(models.SalesOrder).filter(
                models.SalesOrder.sales_order_id == line.sales_order_id
            ).first()
            if order:
                customer = db.query(models.Cliente).filter(
                    models.Cliente.customer_id == order.customer_id
                ).first()

    if item.machine_id:
        machine = db.query(models.Machine).filter(
            models.Machine.machine_id == item.machine_id
        ).first()

    operator = db.query(models.User).filter(
        models.User.user_id == item.created_by_user_id
    ).first()

    return {
        "line": line, "order": order, "customer": customer,
        "machine": machine, "operator": operator,
    }


def _log_print_event(
    db: Session,
    item,
    printed_by_user_id: int,
    branding_mode: str,
    print_weight_mode: str,
    printed_weight_kg: float,
    reason: str,
    backend_used: str = "browser_preview",
) -> models.LabelPrintEvent:
    last_print = (
        db.query(models.LabelPrintEvent)
        .filter(models.LabelPrintEvent.item_id == item.item_id)
        .order_by(models.LabelPrintEvent.print_number.desc())
        .first()
    )
    next_print_number = 1 if not last_print else last_print.print_number + 1
    is_reprint = next_print_number > 1

    event = models.LabelPrintEvent(
        item_id            = item.item_id,
        print_number       = next_print_number,
        printed_by_user_id = printed_by_user_id,
        label_type         = "roll",
        branding_mode      = branding_mode,
        print_weight_mode  = print_weight_mode,
        printed_weight_kg  = printed_weight_kg,
        is_reprint         = is_reprint,
        backend_used       = backend_used,
        reason             = reason,
        template           = f"{branding_mode}_{print_weight_mode}",  # legacy compat
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


# ── PRINT (register + preview) ────────────────────────────────────────────

@router.post("/{item_code}/print")
def print_label(
    item_code:   str,
    payload:     schemas.LabelPrintRequest,
    db:          Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Log a print event and return the preview URL for browser/USB printing.
    The station PC opens the preview URL and prints via the local Zebra USB printer.
    """
    item = db.query(models.Item).filter(models.Item.item_code == item_code).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item no encontrado")

    if item.status in ("cancelled", "replaced"):
        raise HTTPException(
            status_code=400,
            detail=f"Este rollo está {item.status}. "
                   f"{'Reemplazado por: ' + str(item.replaced_by_item_id) if item.replaced_by_item_id else ''}"
        )

    # Use item values unless request overrides
    branding_mode     = payload.branding_mode or item.branding_mode or "normal"
    print_weight_mode = payload.print_weight_mode or item.print_weight_mode or "gross"
    printed_weight_kg = (
        float(item.gross_weight or 0) if print_weight_mode == "gross"
        else float(item.net_weight or 0)
    )

    event = _log_print_event(
        db, item, current_user.user_id,
        branding_mode, print_weight_mode, printed_weight_kg,
        reason=payload.reason,
    )

    barcode_path = build_barcode_png(item.barcode_value or item.item_code, item.item_code)

    return {
        "message":         "Etiqueta registrada",
        "item_code":       item.item_code,
        "barcode_value":   item.barcode_value or item.item_code,
        "print_number":    event.print_number,
        "is_reprint":      event.is_reprint,
        "branding_mode":   branding_mode,
        "print_weight_mode": print_weight_mode,
        "printed_weight_kg": printed_weight_kg,
        "reason":          payload.reason,
        "barcode_path":    barcode_path,
        "preview_url":     f"/labels/{item_code}/preview?autoprint=1",
        "printed_by":      current_user.username,
    }


# ── REPRINT (operator-accessible, no approval) ───────────────────────────

@router.post("/{item_code}/reprint")
def reprint_label(
    item_code:   str,
    payload:     schemas.LabelReprintRequest,
    db:          Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Reprint a label. Operator can do this without supervisor approval.
    Does NOT change any item data. Creates a new LabelPrintEvent with is_reprint=True.
    """
    item = db.query(models.Item).filter(models.Item.item_code == item_code).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item no encontrado")

    if item.status == "cancelled":
        raise HTTPException(status_code=400, detail="No se puede reimprimir un item cancelado")
    if item.status == "replaced":
        repl = db.query(models.Item).filter(
            models.Item.item_id == item.replaced_by_item_id
        ).first()
        msg = "Este rollo fue anulado/reemplazado."
        if repl:
            msg += f" Reemplazado por: {repl.item_code}"
        raise HTTPException(status_code=400, detail=msg)

    branding_mode     = item.branding_mode or "normal"
    print_weight_mode = item.print_weight_mode or "gross"
    printed_weight_kg = (
        float(item.gross_weight or 0) if print_weight_mode == "gross"
        else float(item.net_weight or 0)
    )

    event = _log_print_event(
        db, item, current_user.user_id,
        branding_mode, print_weight_mode, printed_weight_kg,
        reason=payload.reason,
    )

    barcode_path = build_barcode_png(item.barcode_value or item.item_code, item.item_code)

    return {
        "message":         "Reimpresión registrada",
        "item_code":       item.item_code,
        "barcode_value":   item.barcode_value or item.item_code,
        "print_number":    event.print_number,
        "is_reprint":      True,
        "branding_mode":   branding_mode,
        "print_weight_mode": print_weight_mode,
        "printed_weight_kg": printed_weight_kg,
        "barcode_path":    barcode_path,
        "preview_url":     f"/labels/{item_code}/preview?autoprint=1",
        "reprinted_by":    current_user.username,
        "reason":          payload.reason,
    }


# ── LABEL HISTORY ─────────────────────────────────────────────────────────

@router.get("/{item_code}/history")
def get_label_history(
    item_code:   str,
    db:          Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    item = db.query(models.Item).filter(models.Item.item_code == item_code).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item no encontrado")

    prints = (
        db.query(models.LabelPrintEvent)
        .filter(models.LabelPrintEvent.item_id == item.item_id)
        .order_by(models.LabelPrintEvent.print_number.asc())
        .all()
    )

    return [
        {
            "print_event_id":   p.print_event_id,
            "print_number":     p.print_number,
            "printed_by_user_id": p.printed_by_user_id,
            "label_type":       p.label_type,
            "branding_mode":    p.branding_mode,
            "print_weight_mode": p.print_weight_mode,
            "printed_weight_kg": float(p.printed_weight_kg) if p.printed_weight_kg else None,
            "is_reprint":       p.is_reprint,
            "reason":           p.reason,
            "printed_at":       p.printed_at,
        }
        for p in prints
    ]


# ── LABEL PREVIEW (browser/USB print) ────────────────────────────────────

@router.get("/{item_code}/preview")
def preview_label(
    item_code:   str,
    request:     Request,
    db:          Session = Depends(get_db),
):
    """Render a printable HTML label preview. The station PC sends this to the local Zebra USB printer."""
    item = db.query(models.Item).filter(models.Item.item_code == item_code).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item no encontrado")

    ctx = _resolve_label_context(item, db)

    last_print = (
        db.query(models.LabelPrintEvent)
        .filter(models.LabelPrintEvent.item_id == item.item_id)
        .order_by(models.LabelPrintEvent.print_number.desc())
        .first()
    )

    branding_mode     = item.branding_mode or "normal"
    print_weight_mode = item.print_weight_mode or "gross"
    printed_weight_kg = float(item.printed_weight_kg or item.gross_weight or 0)
    print_number      = last_print.print_number if last_print else 0
    is_reprint        = print_number > 1

    barcode_path = build_barcode_png(item.barcode_value or item.item_code, item.item_code)
    stage_label  = STAGE_LABELS.get(item.current_stage or "", "PRODUCTO")

    return templates.TemplateResponse(
        request=request,
        name="label_preview.html",
        context={
            "item":              item,
            "line":              ctx["line"],
            "order":             ctx["order"],
            "customer":          ctx["customer"],
            "machine":           ctx["machine"],
            "operator":          ctx["operator"],
            "barcode_path":      barcode_path,
            "print_number":      print_number,
            "is_reprint":        is_reprint,
            "branding_mode":     branding_mode,
            "print_weight_mode": print_weight_mode,
            "printed_weight_kg": printed_weight_kg,
            "stage_label":       stage_label,
            "logo_url":          "/static/logobiotecnica.jpg",
        },
    )
