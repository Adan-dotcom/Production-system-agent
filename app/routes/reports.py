import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db import get_db
from app import models
from app.auth import get_current_user

router = APIRouter()


def _build_filters(db, sae_order_number, customer_name, machine_id, operator_id,
                   stage, date_from, date_to, include_cancelled):
    query = db.query(models.Item)
    if not include_cancelled:
        query = query.filter(models.Item.status != "cancelled")
    if stage:
        query = query.filter(models.Item.current_stage == stage)
    if date_from:
        query = query.filter(models.Item.created_at >= datetime.fromisoformat(date_from))
    if date_to:
        query = query.filter(models.Item.created_at <= datetime.fromisoformat(date_to))
    items = query.order_by(models.Item.created_at.desc()).all()

    result = []
    for item in items:
        lot = db.query(models.ProductionLot).filter(models.ProductionLot.lot_id == item.lot_id).first()
        so  = db.query(models.SalesOrder).filter(models.SalesOrder.sales_order_id == (lot.sales_order_id if lot else -1)).first() if lot else None
        customer = db.query(models.Cliente).filter(models.Cliente.customer_id == so.customer_id).first() if so else None

        if sae_order_number and (not so or so.sae_order_number != sae_order_number):
            continue
        if customer_name and (not customer or customer_name.lower() not in customer.name.lower()):
            continue

        line   = db.query(models.SalesOrderLine).filter(models.SalesOrderLine.sales_order_line_id == item.sales_order_line_id).first() if item.sales_order_line_id else None
        events = db.query(models.StageEvent).filter(models.StageEvent.item_id == item.item_id).order_by(models.StageEvent.event_id.desc()).all()

        if machine_id and not any(e.machine_id == machine_id for e in events):
            continue
        if operator_id and not any(e.operator_id == operator_id for e in events):
            continue

        last_event   = events[0] if events else None
        machine_name = ""
        if last_event and last_event.machine_id:
            m = db.query(models.Machine).filter(models.Machine.machine_id == last_event.machine_id).first()
            machine_name = m.machine_name if m else ""
        operator_name = ""
        if last_event and last_event.operator_id:
            u = db.query(models.User).filter(models.User.user_id == last_event.operator_id).first()
            operator_name = u.full_name if u else ""

        prints       = db.query(models.LabelPrintEvent).filter(models.LabelPrintEvent.item_id == item.item_id).all()
        reprint_count = sum(1 for p in prints if p.print_number > 1)
        cancellation  = db.query(models.ItemCancellation).filter(models.ItemCancellation.item_id == item.item_id).first()

        result.append({
            "item_code":           item.item_code,
            "roll_number":         item.roll_number,
            "barcode_value":       item.barcode_value or item.item_code,
            "sae_order_number":    so.sae_order_number if so else "",
            "customer_name":       customer.name if customer else "",
            "product_description": line.product_description if line else "",
            "measure":             line.measure if line else "",
            "caliber":             line.caliber if line else "",
            "current_stage":       item.current_stage,
            "status":              item.status,
            "gross_weight":        float(item.gross_weight) if item.gross_weight else 0,
            "net_weight":          float(item.net_weight)   if item.net_weight   else 0,
            "printed_weight_kg":   float(item.printed_weight_kg) if item.printed_weight_kg else 0,
            "bobbin_weight_kg":    float(item.bobbin_weight_kg)  if item.bobbin_weight_kg  else 0,
            "branding_mode":       item.branding_mode or "normal",
            "print_weight_mode":   item.print_weight_mode or "gross",
            "shift":               item.shift or "",
            "machine_name":        machine_name,
            "operator_name":       operator_name,
            "created_at":          item.created_at.isoformat() if item.created_at else "",
            "print_count":         len(prints),
            "reprint_count":       reprint_count,
            "cancellation_reason": cancellation.reason if cancellation else "",
            "cancelled_at":        cancellation.cancelled_at.isoformat() if cancellation else "",
        })
    return result


# ── Summary (JSON) ────────────────────────────────────────────────────────────
@router.get("/summary")
def get_summary(
    sae_order_number: Optional[str] = Query(None),
    customer_name:    Optional[str] = Query(None),
    machine_id:       Optional[int] = Query(None),
    operator_id:      Optional[int] = Query(None),
    stage:            Optional[str] = Query(None),
    date_from:        Optional[str] = Query(None),
    date_to:          Optional[str] = Query(None),
    include_cancelled: bool         = Query(False),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    rows = _build_filters(db, sae_order_number, customer_name, machine_id,
                          operator_id, stage, date_from, date_to, include_cancelled)
    return {
        "total_items":    len(rows),
        "total_gross_kg": round(sum(r["gross_weight"] for r in rows), 3),
        "total_net_kg":   round(sum(r["net_weight"]   for r in rows), 3),
        "total_reprints": sum(r["reprint_count"] for r in rows),
        "total_cancelled": sum(1 for r in rows if r["status"] == "cancelled"),
    }


# ── Export CSV ────────────────────────────────────────────────────────────────
@router.get("/export/csv")
def export_csv(
    sae_order_number: Optional[str] = Query(None),
    customer_name:    Optional[str] = Query(None),
    machine_id:       Optional[int] = Query(None),
    operator_id:      Optional[int] = Query(None),
    stage:            Optional[str] = Query(None),
    date_from:        Optional[str] = Query(None),
    date_to:          Optional[str] = Query(None),
    include_cancelled: bool         = Query(False),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    rows = _build_filters(db, sae_order_number, customer_name, machine_id,
                          operator_id, stage, date_from, date_to, include_cancelled)
    output = io.StringIO()
    fieldnames = [
        "item_code","roll_number","barcode_value","sae_order_number","customer_name",
        "product_description","measure","caliber","current_stage","status",
        "gross_weight","bobbin_weight_kg","net_weight","printed_weight_kg",
        "branding_mode","print_weight_mode","shift",
        "machine_name","operator_name","created_at",
        "print_count","reprint_count","cancellation_reason","cancelled_at",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)
    filename = f"produccion_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── NEW: Progress by PI ───────────────────────────────────────────────────────
@router.get("/by-pi")
def report_by_pi(
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Per-order progress: kg produced vs target_kg.
    Returns list sorted by sae_order_number.
    """
    orders = db.query(models.SalesOrder).order_by(models.SalesOrder.sae_order_number).all()
    result = []
    for so in orders:
        customer = db.query(models.Cliente).filter(models.Cliente.customer_id == so.customer_id).first()
        lines    = db.query(models.SalesOrderLine).filter(
            models.SalesOrderLine.sales_order_id == so.sales_order_id
        ).all()

        line_data = []
        total_produced_gross = 0.0
        total_produced_net   = 0.0
        total_rolls          = 0
        total_cancelled      = 0

        for line in lines:
            # All non-cancelled items for this line
            q = db.query(models.Item).filter(
                models.Item.sales_order_line_id == line.sales_order_line_id
            )
            if date_from:
                q = q.filter(models.Item.created_at >= datetime.fromisoformat(date_from))
            if date_to:
                q = q.filter(models.Item.created_at <= datetime.fromisoformat(date_to))
            all_items = q.all()

            # Count FINISHED goods only (almacén). Each physical roll spawns one Item
            # per station, all linked to this line; counting every stage would inflate
            # produced kg ~Nx. Progress is measured at the warehouse, like by-sae.
            active_items    = [i for i in all_items
                               if i.status not in ("cancelled","replaced","rejected")
                               and i.current_stage == "almacen"]
            cancelled_items = [i for i in all_items if i.status == "cancelled"]

            line_gross = sum(float(i.gross_weight or 0) for i in active_items)
            line_net   = sum(float(i.net_weight   or 0) for i in active_items)
            line_rolls = len(active_items)

            # target from KILOSxPARTIDA or ordered_kg
            target = float(line.target_kg or line.ordered_kg or 0)
            pct    = round((line_net / target * 100), 1) if target > 0 else None

            total_produced_gross += line_gross
            total_produced_net   += line_net
            total_rolls          += line_rolls
            total_cancelled      += len(cancelled_items)

            line_data.append({
                "sales_order_line_id": line.sales_order_line_id,
                "line_number":         line.line_number,
                "product_description": line.product_description,
                "product_code":        line.product_code,
                "measure":             line.measure,
                "caliber":             line.caliber,
                "target_kg":           target if target > 0 else None,
                "produced_gross_kg":   round(line_gross, 3),
                "produced_net_kg":     round(line_net,   3),
                "roll_count":          line_rolls,
                "cancelled_count":     len(cancelled_items),
                "pct_complete":        pct,
            })

        # Order-level target
        order_target = float(so.target_kg or so.ordered_kg or 0)
        order_pct    = round((total_produced_net / order_target * 100), 1) if order_target > 0 else None

        result.append({
            "sales_order_id":      so.sales_order_id,
            "sae_order_number":    so.sae_order_number,
            "customer_name":       customer.name if customer else "",
            "order_status":        so.order_status or "—",
            "delivery_date":       so.delivery_date.strftime("%d/%m/%Y") if so.delivery_date else None,
            "order_target_kg":     order_target if order_target > 0 else None,
            "produced_gross_kg":   round(total_produced_gross, 3),
            "produced_net_kg":     round(total_produced_net,   3),
            "total_rolls":         total_rolls,
            "total_cancelled":     total_cancelled,
            "pct_complete":        order_pct,
            "lines":               line_data,
        })

    return result


# ── NEW: By operator ──────────────────────────────────────────────────────────
@router.get("/by-operator")
def report_by_operator(
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Production totals grouped by operator."""
    q = db.query(models.Item).filter(models.Item.status != "cancelled")
    if date_from:
        q = q.filter(models.Item.created_at >= datetime.fromisoformat(date_from))
    if date_to:
        q = q.filter(models.Item.created_at <= datetime.fromisoformat(date_to))
    items = q.all()

    by_op: dict[int, dict] = {}
    for item in items:
        uid = item.created_by_user_id
        if uid not in by_op:
            user = db.query(models.User).filter(models.User.user_id == uid).first()
            by_op[uid] = {
                "operator_id":   uid,
                "operator_name": user.full_name if user else f"ID {uid}",
                "roll_count":    0,
                "gross_kg":      0.0,
                "net_kg":        0.0,
            }
        by_op[uid]["roll_count"] += 1
        by_op[uid]["gross_kg"]   += float(item.gross_weight  or 0)
        by_op[uid]["net_kg"]     += float(item.net_weight    or 0)

    result = sorted(by_op.values(), key=lambda x: x["net_kg"], reverse=True)
    for r in result:
        r["gross_kg"] = round(r["gross_kg"], 3)
        r["net_kg"]   = round(r["net_kg"],   3)
    return result


# ── NEW: By shift ─────────────────────────────────────────────────────────────
@router.get("/by-shift")
def report_by_shift(
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Production totals grouped by shift (T1/T2/T3)."""
    q = db.query(models.Item).filter(models.Item.status != "cancelled")
    if date_from:
        q = q.filter(models.Item.created_at >= datetime.fromisoformat(date_from))
    if date_to:
        q = q.filter(models.Item.created_at <= datetime.fromisoformat(date_to))
    items = q.all()

    by_shift: dict[str, dict] = {}
    for item in items:
        shift = item.shift or "—"
        if shift not in by_shift:
            by_shift[shift] = {"shift": shift, "roll_count": 0, "gross_kg": 0.0, "net_kg": 0.0}
        by_shift[shift]["roll_count"] += 1
        by_shift[shift]["gross_kg"]   += float(item.gross_weight or 0)
        by_shift[shift]["net_kg"]     += float(item.net_weight   or 0)

    order = ["T1", "T2", "T3", "—"]
    result = sorted(by_shift.values(), key=lambda x: order.index(x["shift"]) if x["shift"] in order else 99)
    for r in result:
        r["gross_kg"] = round(r["gross_kg"], 3)
        r["net_kg"]   = round(r["net_kg"],   3)
    return result
