from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app import models, schemas
from app.auth import get_current_user
from app.routes.labels import build_barcode_png

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def generate_pallet_code(db: Session) -> str:
    count = db.query(models.Pallet).count() + 1
    return f"PAL-{datetime.now().strftime('%Y%m%d')}-{count:05d}"


def _recalc_pallet_totals(pallet: models.Pallet, db: Session):
    """Recompute gross/net/printed totals from pallet_items."""
    rows = db.query(models.PalletItem).filter(
        models.PalletItem.pallet_id == pallet.pallet_id
    ).all()
    total_gross   = 0.0
    total_net     = 0.0
    total_printed = 0.0
    for row in rows:
        item = db.query(models.Item).filter(models.Item.item_id == row.item_id).first()
        if item:
            total_gross   += float(item.gross_weight or 0)
            total_net     += float(item.net_weight or 0)
            total_printed += float(item.printed_weight_kg or item.gross_weight or 0)
    pallet.total_kg        = total_gross   # legacy compat
    pallet.total_gross_kg  = total_gross
    pallet.total_net_kg    = total_net
    pallet.total_printed_kg = total_printed


def _build_summary_by_line(pallet: models.Pallet, db: Session) -> list:
    """Group pallet contents by sales_order_line."""
    rows = db.query(models.PalletItem).filter(
        models.PalletItem.pallet_id == pallet.pallet_id
    ).all()

    by_line: dict[int | None, dict] = {}
    for row in rows:
        item = db.query(models.Item).filter(models.Item.item_id == row.item_id).first()
        if not item:
            continue
        key = item.sales_order_line_id
        if key not in by_line:
            line = None
            if key:
                line = db.query(models.SalesOrderLine).filter(
                    models.SalesOrderLine.sales_order_line_id == key
                ).first()
            by_line[key] = {
                "sales_order_line_id":  key,
                "product_description":  line.product_description if line else "—",
                "measure":              line.measure if line else "—",
                "caliber":              line.caliber if line else "—",
                "color":                line.color if line else None,
                "roll_count":           0,
                "gross_weight_total":   0.0,
                "net_weight_total":     0.0,
                "printed_weight_total": 0.0,
                "item_codes":           [],
            }
        g = float(item.gross_weight or 0)
        n = float(item.net_weight or 0)
        p = float(item.printed_weight_kg or item.gross_weight or 0)
        by_line[key]["roll_count"]           += 1
        by_line[key]["gross_weight_total"]   += g
        by_line[key]["net_weight_total"]     += n
        by_line[key]["printed_weight_total"] += p
        by_line[key]["item_codes"].append(item.item_code)

    return list(by_line.values())


def _pallet_response(pallet: models.Pallet, db: Session) -> dict:
    order    = db.query(models.SalesOrder).filter(
        models.SalesOrder.sales_order_id == pallet.sales_order_id
    ).first()
    customer = None
    if order:
        customer = db.query(models.Cliente).filter(
            models.Cliente.customer_id == order.customer_id
        ).first()

    rows  = db.query(models.PalletItem).filter(
        models.PalletItem.pallet_id == pallet.pallet_id
    ).all()
    items = []
    for row in rows:
        item = db.query(models.Item).filter(models.Item.item_id == row.item_id).first()
        if item:
            items.append({
                "item_code":        item.item_code,
                "barcode_value":    item.barcode_value or item.item_code,
                "gross_weight":     float(item.gross_weight or 0),
                "net_weight":       float(item.net_weight or 0),
                "printed_weight_kg": float(item.printed_weight_kg or item.gross_weight or 0),
                "status":           item.status,
                "sales_order_line_id": item.sales_order_line_id,
            })

    summary_by_line = _build_summary_by_line(pallet, db)

    scanned_items = [
        {"item_code": it["item_code"], "gross_weight": it["gross_weight"]}
        for it in items
    ]

    return {
        "pallet_id":         pallet.pallet_id,
        "pallet_code":       pallet.pallet_code,
        "sales_order_id":    pallet.sales_order_id,
        "sae_order_number":  order.sae_order_number if order else "",
        "customer_name":     customer.name if customer else "",
        "status":            pallet.status,
        "total_gross_kg":    float(pallet.total_gross_kg or pallet.total_kg or 0),
        "total_net_kg":      float(pallet.total_net_kg or 0),
        "total_printed_kg":  float(pallet.total_printed_kg or 0),
        "total_kg":          float(pallet.total_kg or 0),
        "pallet_weight_mode": pallet.pallet_weight_mode or "gross",
        "created_at":        pallet.created_at.isoformat() if pallet.created_at else None,
        "closed_at":         pallet.closed_at.isoformat() if pallet.closed_at else None,
        "items":             items,
        "scanned_items":     scanned_items,
        "summary_by_line":   summary_by_line,
    }


# ── CREATE PALLET ─────────────────────────────────────────────────────────

@router.post("/")
def create_pallet(
    payload:     schemas.PalletCreateRequest,
    db:          Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    order = db.query(models.SalesOrder).filter(
        models.SalesOrder.sales_order_id == payload.sales_order_id
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")

    pallet = models.Pallet(
        pallet_code        = generate_pallet_code(db),
        sales_order_id     = payload.sales_order_id,
        sales_order_line_id= payload.sales_order_line_id,
        total_kg           = 0,
        total_gross_kg     = 0,
        total_net_kg       = 0,
        total_printed_kg   = 0,
        status             = "open",
        created_by_user_id = current_user.user_id,
    )
    db.add(pallet)
    db.commit()
    db.refresh(pallet)

    return {
        "message":     "Tarima creada",
        "pallet_id":   pallet.pallet_id,
        "pallet_code": pallet.pallet_code,
        "status":      pallet.status,
    }


# ── ADD ITEM TO PALLET ────────────────────────────────────────────────────

@router.post("/{pallet_code}/items")
def add_item_to_pallet(
    pallet_code: str,
    payload:     schemas.PalletAddItemRequest,
    db:          Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    pallet = db.query(models.Pallet).filter(models.Pallet.pallet_code == pallet_code).first()
    if not pallet:
        raise HTTPException(status_code=404, detail="Tarima no encontrada")

    if pallet.status == "closed":
        raise HTTPException(status_code=400, detail="La tarima ya está cerrada. No se pueden agregar más rollos.")

    item = db.query(models.Item).filter(
        (models.Item.item_code == payload.item_code) |
        (models.Item.barcode_value == payload.item_code)
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail=f"Rollo no encontrado: {payload.item_code}")

    # Validate item status
    if item.status == "cancelled":
        raise HTTPException(status_code=400, detail="Este rollo fue anulado. No se puede agregar a tarima.")

    if item.status == "replaced":
        repl = db.query(models.Item).filter(models.Item.item_id == item.replaced_by_item_id).first()
        msg = "Este rollo fue anulado/reemplazado."
        if repl:
            msg += f" Usa el rollo de reemplazo: {repl.item_code}"
        raise HTTPException(status_code=400, detail=msg)

    if item.status == "rejected":
        raise HTTPException(status_code=400, detail="Este rollo fue enviado a reciclaje.")

    # Validate same PI
    if item.sales_order_line_id:
        line = db.query(models.SalesOrderLine).filter(
            models.SalesOrderLine.sales_order_line_id == item.sales_order_line_id
        ).first()
        if line and line.sales_order_id != pallet.sales_order_id:
            raise HTTPException(
                status_code=400,
                detail="Este rollo pertenece a un PI diferente al de la tarima."
            )

    # Check not already on another pallet
    existing_entry = db.query(models.PalletItem).filter(
        models.PalletItem.item_id == item.item_id
    ).first()
    if existing_entry:
        if existing_entry.pallet_id == pallet.pallet_id:
            raise HTTPException(status_code=400, detail="El rollo ya está en esta tarima.")
        raise HTTPException(status_code=400, detail="Este rollo ya está en otra tarima.")

    pi = models.PalletItem(
        pallet_id        = pallet.pallet_id,
        item_id          = item.item_id,
        added_by_user_id = current_user.user_id,
    )
    db.add(pi)
    db.commit()

    _recalc_pallet_totals(pallet, db)
    db.commit()

    return {
        "message":       "Rollo agregado a tarima",
        "pallet_code":   pallet.pallet_code,
        "item_code":     item.item_code,
        "barcode_value": item.barcode_value or item.item_code,
        "total_gross_kg": float(pallet.total_gross_kg or 0),
        "total_net_kg":  float(pallet.total_net_kg or 0),
    }


# ── LIST PALLETS ─────────────────────────────────────────────────────────

@router.get("/")
def list_pallets(
    status: str | None = None,   # "open" | "closed" | None = todos
    sae:    str | None = None,   # filtrar por sae_order_number (CVE_DOC)
    limit:  int        = 50,
    db:     Session    = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Lista ligera de tarimas para el panel admin y almacén."""
    q = db.query(models.Pallet).order_by(models.Pallet.created_at.desc())
    if status:
        q = q.filter(models.Pallet.status == status)
    if sae:
        so = db.query(models.SalesOrder).filter(models.SalesOrder.sae_order_number == sae).first()
        if so:
            q = q.filter(models.Pallet.sales_order_id == so.sales_order_id)
        else:
            return []   # PI no encontrado → lista vacía
    pallets = q.limit(limit).all()

    result = []
    for p in pallets:
        order    = db.query(models.SalesOrder).filter(models.SalesOrder.sales_order_id == p.sales_order_id).first()
        customer = db.query(models.Cliente).filter(models.Cliente.customer_id == order.customer_id).first() if order else None
        item_count = db.query(models.PalletItem).filter(models.PalletItem.pallet_id == p.pallet_id).count()
        result.append({
            "pallet_id":        p.pallet_id,
            "pallet_code":      p.pallet_code,
            "sae_order_number": order.sae_order_number if order else "—",
            "customer_name":    customer.name if customer else "—",
            "status":           p.status,
            "item_count":       item_count,
            "total_gross_kg":   float(p.total_gross_kg or p.total_kg or 0),
            "total_net_kg":     float(p.total_net_kg or 0),
            "created_at":       p.created_at.isoformat() if p.created_at else None,
            "closed_at":        p.closed_at.isoformat() if p.closed_at else None,
        })
    return result


# ── GET PALLET ────────────────────────────────────────────────────────────

@router.get("/{pallet_code}")
def get_pallet(
    pallet_code: str,
    db:          Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    pallet = db.query(models.Pallet).filter(models.Pallet.pallet_code == pallet_code).first()
    if not pallet:
        raise HTTPException(status_code=404, detail="Tarima no encontrada")
    return _pallet_response(pallet, db)


# ── GET OPEN PALLET FOR ORDER ─────────────────────────────────────────────

@router.get("/open-for-order/{sae_order_number}")
def get_open_pallet_for_order(
    sae_order_number: str,
    db:               Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    so = db.query(models.SalesOrder).filter(
        models.SalesOrder.sae_order_number == sae_order_number
    ).first()
    if not so:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")

    pallet = (
        db.query(models.Pallet)
        .filter(models.Pallet.sales_order_id == so.sales_order_id, models.Pallet.status == "open")
        .order_by(models.Pallet.created_at.desc())
        .first()
    )
    if not pallet:
        raise HTTPException(status_code=404, detail="No hay tarima abierta para este pedido")

    return _pallet_response(pallet, db)


# ── CREATE PALLET FOR ORDER ───────────────────────────────────────────────

@router.post("/create-for-order/{sae_order_number}")
def create_pallet_for_order(
    sae_order_number: str,
    db:               Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    so = db.query(models.SalesOrder).filter(
        models.SalesOrder.sae_order_number == sae_order_number
    ).first()
    if not so:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")

    pallet = models.Pallet(
        pallet_code        = generate_pallet_code(db),
        sales_order_id     = so.sales_order_id,
        total_kg           = 0,
        total_gross_kg     = 0,
        total_net_kg       = 0,
        total_printed_kg   = 0,
        status             = "open",
        created_by_user_id = current_user.user_id,
    )
    db.add(pallet)
    db.commit()
    db.refresh(pallet)

    customer = db.query(models.Cliente).filter(
        models.Cliente.customer_id == so.customer_id
    ).first()

    return _pallet_response(pallet, db)


# ── CLOSE PALLET ──────────────────────────────────────────────────────────

@router.post("/{pallet_code}/close")
def close_pallet(
    pallet_code: str,
    db:          Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    pallet = db.query(models.Pallet).filter(models.Pallet.pallet_code == pallet_code).first()
    if not pallet:
        raise HTTPException(status_code=404, detail="Tarima no encontrada")
    if pallet.status == "closed":
        raise HTTPException(status_code=400, detail="La tarima ya está cerrada")

    rows = db.query(models.PalletItem).filter(
        models.PalletItem.pallet_id == pallet.pallet_id
    ).all()
    if not rows:
        raise HTTPException(status_code=400, detail="No se puede cerrar una tarima vacía")

    # Recompute totals before closing
    _recalc_pallet_totals(pallet, db)

    pallet.status             = "closed"
    pallet.closed_at          = datetime.now()
    pallet.closed_by_user_id  = current_user.user_id
    db.commit()

    return {
        "message":         "Tarima cerrada",
        "pallet_code":     pallet.pallet_code,
        "total_gross_kg":  float(pallet.total_gross_kg or 0),
        "total_net_kg":    float(pallet.total_net_kg or 0),
        "total_printed_kg": float(pallet.total_printed_kg or 0),
        "item_count":      len(rows),
        "closed_at":       pallet.closed_at.isoformat(),
        "closed_by":       current_user.username,
        "preview_url":     f"/pallets/{pallet_code}/preview",
    }


# ── PALLET LABEL PREVIEW ──────────────────────────────────────────────────

@router.get("/{pallet_code}/preview")
def preview_pallet_label(
    pallet_code: str,
    request:     Request,
    db:          Session = Depends(get_db),
):
    pallet = db.query(models.Pallet).filter(models.Pallet.pallet_code == pallet_code).first()
    if not pallet:
        raise HTTPException(status_code=404, detail="Tarima no encontrada")

    order    = db.query(models.SalesOrder).filter(
        models.SalesOrder.sales_order_id == pallet.sales_order_id
    ).first()
    customer = None
    if order:
        customer = db.query(models.Cliente).filter(
            models.Cliente.customer_id == order.customer_id
        ).first()

    rows  = db.query(models.PalletItem).filter(
        models.PalletItem.pallet_id == pallet.pallet_id
    ).all()
    items = [
        db.query(models.Item).filter(models.Item.item_id == row.item_id).first()
        for row in rows
    ]
    items = [i for i in items if i]  # filter None

    summary_by_line = _build_summary_by_line(pallet, db)
    barcode_path    = build_barcode_png(pallet.pallet_code, pallet.pallet_code)

    return templates.TemplateResponse(
        request=request,
        name="pallet_label_preview.html",
        context={
            "pallet":           pallet,
            "order":            order,
            "customer":         customer,
            "items":            items,
            "summary_by_line":  summary_by_line,
            "total_rolls":      len(items),
            "total_gross_kg":   float(pallet.total_gross_kg or pallet.total_kg or 0),
            "total_net_kg":     float(pallet.total_net_kg or 0),
            "total_printed_kg": float(pallet.total_printed_kg or 0),
            "barcode_path":     barcode_path,
            "logo_url":         "/static/logobiotecnica.jpg",
        },
    )
