import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db import get_db
from app import models, schemas
from app.auth import get_current_user

router = APIRouter()

VALID_STAGES = {"extrusion", "corte", "impresion", "bolseo", "almacen", "reciclaje"}

ALLOWED_NEXT_STAGE = {
    "extrusion": ["corte", "impresion", "bolseo", "almacen", "reciclaje"],
    "corte":     ["impresion", "bolseo", "almacen", "reciclaje"],
    "impresion": ["bolseo", "almacen", "reciclaje"],
    "bolseo":    ["almacen", "reciclaje"],
    "almacen":   [],
    "reciclaje": [],
}


def get_shift(dt: datetime) -> str:
    h = dt.hour
    if 6 <= h < 14:
        return "T1"
    elif 14 <= h < 22:
        return "T2"
    return "T3"


def generate_item_code(db: Session) -> str:
    count = db.query(func.count(models.Item.item_id)).scalar() or 0
    seq   = count + 1
    return f"IT-{datetime.now().strftime('%Y%m%d')}-{seq:06d}"


def generate_roll_number(db: Session) -> int:
    max_roll = db.query(func.max(models.Item.roll_number)).scalar() or 0
    return max_roll + 1


def measure_width_cm(measure: Optional[str]) -> Optional[float]:
    """Extract the leading width number (cm) from a parsed measure string.

    The measure parser stores values like '245', '030X040' or '116+100X175'.
    We take the first numeric token as the roll width (in cm), which is what
    drives both the threshold band and the derived bobbin length.
    """
    if not measure:
        return None
    m = re.search(r"\d+(?:\.\d+)?", str(measure))
    return float(m.group(0)) if m else None


def bobbin_length_m(rule: "models.TareRule", line: Optional["models.SalesOrderLine"]) -> float:
    """Length (m) used for linear-density tare.

    Priority: explicit default_length_m on the rule, otherwise derived from the
    roll measure width (cm → m). The bobbin/core length matches the film width.
    """
    if rule.default_length_m is not None:
        return float(rule.default_length_m)
    width = measure_width_cm(line.measure) if line else None
    return (width / 100.0) if width else 0.0


def tare_from_rule(rule: "models.TareRule", line: Optional["models.SalesOrderLine"]) -> float:
    """Compute the tare (kg) a resolved rule yields for a given order line."""
    if rule.rule_type == "linear_density":
        density = float(rule.linear_density_kg_per_m or 0)
        return density * bobbin_length_m(rule, line)
    # fixed / threshold / manual: fixed_weight_kg if set, else 0
    return float(rule.fixed_weight_kg or 0)


def match_threshold_rule(db: Session, width_cm: Optional[float]) -> Optional["models.TareRule"]:
    """Pick the active rule whose threshold band contains the roll width (cm).

    Lets several linear_density rules coexist, each scoped to a width range,
    so the parsed measure decides which roll → which density → which tare.
    """
    if width_cm is None:
        return None
    candidates = db.query(models.TareRule).filter(
        models.TareRule.is_active == True,
        models.TareRule.threshold_field.isnot(None),
    ).all()
    for r in candidates:
        lo = float(r.threshold_min) if r.threshold_min is not None else None
        hi = float(r.threshold_max) if r.threshold_max is not None else None
        if (lo is None or width_cm >= lo) and (hi is None or width_cm <= hi):
            return r
    return None


def resolve_tare(
    db: Session,
    tare_rule_id: Optional[int],
    bobbin_weight_kg_manual: Optional[float],
    sales_order_line_id: Optional[int],
    station: str,
) -> float:
    """
    Resolve tare weight (kg) using the following priority:
    1. Manual override (bobbin_weight_kg_manual)
    2. Explicit tare_rule_id
    3. Default rule from sales_order_line
    4. Threshold band matched against the parsed roll measure (width cm)
    5. Global default tare rule
    6. Fallback 0 (no tare)
    """
    if bobbin_weight_kg_manual is not None:
        return float(bobbin_weight_kg_manual)

    line = None
    if sales_order_line_id:
        line = db.query(models.SalesOrderLine).filter(
            models.SalesOrderLine.sales_order_line_id == sales_order_line_id
        ).first()

    rule = None

    # 1. Explicit rule id
    if tare_rule_id:
        rule = db.query(models.TareRule).filter(
            models.TareRule.id == tare_rule_id, models.TareRule.is_active == True
        ).first()

    # 2. Default rule from order line
    if rule is None and line:
        if line.default_tare_rule_id:
            rule = db.query(models.TareRule).filter(
                models.TareRule.id == line.default_tare_rule_id,
                models.TareRule.is_active == True,
            ).first()
        # Legacy: per-line bobbin_weight_kg
        if rule is None and line.require_bobbin_tare and line.bobbin_weight_kg:
            return float(line.bobbin_weight_kg)

    # 3. Threshold band matched against the parsed measure width
    if rule is None and line is not None:
        rule = match_threshold_rule(db, measure_width_cm(line.measure))

    # 4. Global default
    if rule is None:
        rule = db.query(models.TareRule).filter(
            models.TareRule.is_default == True, models.TareRule.is_active == True
        ).first()

    if rule is None:
        return 0.0

    return tare_from_rule(rule, line)


def _item_to_dict(item, db: Session) -> dict:
    """Full item representation with joined data."""
    line = order = customer = machine = operator = cancellation = None

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

    cancellation = db.query(models.ItemCancellation).filter(
        models.ItemCancellation.item_id == item.item_id
    ).order_by(models.ItemCancellation.cancellation_id.desc()).first()

    return {
        "item_id":           item.item_id,
        "item_code":         item.item_code,
        "roll_number":       item.roll_number,
        "barcode_value":     item.barcode_value or item.item_code,
        "status":            item.status,
        "current_stage":     item.current_stage,
        "next_stage":        item.next_stage,
        "sae_order_number":  order.sae_order_number if order else None,
        "customer_name":     customer.name if customer else None,
        "product_description": line.product_description if line else None,
        "measure":           line.measure if line else None,
        "caliber":           line.caliber if line else None,
        "color":             line.color if line else None,
        "sales_order_line_id": item.sales_order_line_id,
        "gross_weight":      float(item.gross_weight) if item.gross_weight is not None else None,
        "bobbin_weight_kg":  float(item.bobbin_weight_kg) if item.bobbin_weight_kg is not None else None,
        "net_weight":        float(item.net_weight) if item.net_weight is not None else None,
        "print_weight_mode": item.print_weight_mode,
        "printed_weight_kg": float(item.printed_weight_kg) if item.printed_weight_kg is not None else None,
        "branding_mode":     item.branding_mode,
        "shift":             item.shift,
        "machine_id":        item.machine_id,
        "machine_name":      machine.machine_name if machine else None,
        "operator_name":     operator.full_name if operator else None,
        "created_by_user_id": item.created_by_user_id,
        "created_at":        item.created_at.isoformat() if item.created_at else None,
        "cancelled_at":      item.cancelled_at.isoformat() if item.cancelled_at else None,
        "cancel_reason":     item.cancel_reason,
        "cancel_notes":      item.cancel_notes,
        "replaces_item_id":  item.replaces_item_id,
        "replaced_by_item_id": item.replaced_by_item_id,
        "cancellation_reason": cancellation.reason if cancellation else None,
    }


# ── RECENT ITEMS (for reprint / history / cancel screens) ─────────────────

@router.get("/recent")
def get_recent_items(
    station:     Optional[str] = Query(None),
    pi:          Optional[str] = Query(None),
    operator_id: Optional[int] = Query(None),
    status:      Optional[str] = Query(None),
    date_from:   Optional[str] = Query(None),
    date_to:     Optional[str] = Query(None),
    limit:       int           = Query(100, le=500),
    db:          Session       = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Return recent production items, newest first. Used by operator reprint/history screen."""
    q = db.query(models.Item)

    if status:
        q = q.filter(models.Item.status == status)

    if station:
        q = q.filter(models.Item.current_stage == station)

    if operator_id:
        q = q.filter(models.Item.created_by_user_id == operator_id)

    if date_from:
        q = q.filter(models.Item.created_at >= datetime.fromisoformat(date_from))
    if date_to:
        q = q.filter(models.Item.created_at <= datetime.fromisoformat(date_to))

    items = q.order_by(models.Item.created_at.desc()).limit(limit).all()

    # Filter by PI (requires join)
    if pi:
        filtered = []
        for item in items:
            if not item.sales_order_line_id:
                continue
            line = db.query(models.SalesOrderLine).filter(
                models.SalesOrderLine.sales_order_line_id == item.sales_order_line_id
            ).first()
            if not line:
                continue
            order = db.query(models.SalesOrder).filter(
                models.SalesOrder.sales_order_id == line.sales_order_id
            ).first()
            if order and pi.upper() in order.sae_order_number.upper():
                filtered.append(item)
        items = filtered

    return [_item_to_dict(item, db) for item in items]


# ── PRODUCE (main production endpoint) ────────────────────────────────────

@router.post("/produce")
def produce_item(
    payload:      schemas.ItemProduceRequest,
    db:           Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create a new labeled production item at any station.
    Handles tare calculation, branding/weight mode, and optional replacement link.
    """
    if payload.station not in VALID_STAGES:
        raise HTTPException(status_code=400, detail=f"Estación inválida: {payload.station}")

    allowed_next = ALLOWED_NEXT_STAGE.get(payload.station, [])
    if payload.next_stage not in allowed_next:
        raise HTTPException(status_code=400, detail=f"Destino '{payload.next_stage}' inválido desde {payload.station}")

    machine = db.query(models.Machine).filter(models.Machine.machine_id == payload.machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Máquina no encontrada")
    if not machine.is_active:
        raise HTTPException(status_code=400, detail="La máquina está inactiva")

    line = db.query(models.SalesOrderLine).filter(
        models.SalesOrderLine.sales_order_line_id == payload.sales_order_line_id
    ).first()
    if not line:
        raise HTTPException(status_code=404, detail="Línea de pedido no encontrada")

    # Validate/resolve label options
    branding_mode = payload.branding_mode if payload.branding_mode in ("normal", "distributor") else "normal"
    print_weight_mode = payload.print_weight_mode if payload.print_weight_mode in ("gross", "net") else "gross"

    # Resolve lot
    if payload.lot_id:
        lot = db.query(models.ProductionLot).filter(
            models.ProductionLot.lot_id == payload.lot_id
        ).first()
        if not lot:
            raise HTTPException(status_code=404, detail="Lote no encontrado")
        if line.sales_order_id != lot.sales_order_id:
            raise HTTPException(status_code=400, detail="La línea no pertenece al lote indicado")
    else:
        lot = db.query(models.ProductionLot).filter(
            models.ProductionLot.sales_order_id == line.sales_order_id
        ).first()
        if not lot:
            so = db.query(models.SalesOrder).filter(
                models.SalesOrder.sales_order_id == line.sales_order_id
            ).first()
            lot = models.ProductionLot(
                sales_order_id=line.sales_order_id,
                lot_code=f"AUTO-{so.sae_order_number if so else line.sales_order_id}",
            )
            db.add(lot)
            db.commit()
            db.refresh(lot)

    # Tare calculation
    bobbin_kg = resolve_tare(
        db,
        tare_rule_id=payload.tare_rule_id,
        bobbin_weight_kg_manual=payload.bobbin_weight_kg,
        sales_order_line_id=payload.sales_order_line_id,
        station=payload.station,
    )

    gross_weight = payload.gross_weight
    net_weight   = gross_weight - bobbin_kg

    if net_weight <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"Peso neto inválido ({net_weight:.3f} kg). "
                   f"Peso bruto {gross_weight} kg − tara {bobbin_kg} kg ≤ 0."
        )

    printed_weight_kg = gross_weight if print_weight_mode == "gross" else net_weight

    now         = datetime.now()
    shift       = get_shift(now)
    item_code   = generate_item_code(db)
    roll_number = generate_roll_number(db)

    # Barcode: <sae_order_number>-<roll_number>
    so = db.query(models.SalesOrder).filter(
        models.SalesOrder.sales_order_id == line.sales_order_id
    ).first()
    barcode_value = f"{so.sae_order_number}-{roll_number}" if so else item_code

    # Status / stage
    if payload.next_stage == "almacen":
        status        = "completed"
        current_stage = "almacen"
    elif payload.next_stage == "reciclaje":
        status        = "rejected"
        current_stage = "reciclaje"
    else:
        status        = "active"
        current_stage = payload.station

    item = models.Item(
        lot_id              = lot.lot_id,
        sales_order_line_id = payload.sales_order_line_id,
        item_code           = item_code,
        roll_number         = roll_number,
        barcode_value       = barcode_value,
        current_stage       = current_stage,
        next_stage          = payload.next_stage,
        status              = status,
        gross_weight        = gross_weight,
        net_weight          = net_weight,
        bobbin_weight_kg    = bobbin_kg,
        print_weight_mode   = print_weight_mode,
        printed_weight_kg   = printed_weight_kg,
        branding_mode       = branding_mode,
        machine_id          = payload.machine_id,
        shift               = shift,
        tare_rule_id        = payload.tare_rule_id,
        created_by_user_id  = current_user.user_id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    # Update completed_kg with FINISHED goods only (warehouse). A roll that passes
    # through several stations creates one Item per station all linked to the same
    # line; counting each would inflate progress ~Nx. We count it once — when it
    # reaches 'almacen' — matching the operator dashboard (/orders/by-sae).
    if current_stage == "almacen":
        line.completed_kg = float(line.completed_kg or 0) + net_weight
        db.commit()

    # Stage event
    event = models.StageEvent(
        item_id          = item.item_id,
        event_type       = "produce",
        from_stage       = None,
        to_stage         = current_stage,
        machine_id       = payload.machine_id,
        operator_id      = current_user.user_id,
        gross_weight_out = gross_weight,
        net_weight_out   = net_weight,
        tare_constant_kg = bobbin_kg,
        shrinkage_kg     = 0,
        tolerance_percent= 5.0,
        discrepancy_flag = False,
        discrepancy_note = f"Producido en {payload.station} → {payload.next_stage}",
        shift            = shift,
    )
    db.add(event)

    # Optional: link replacement
    if payload.replaces_item_code:
        old_item = db.query(models.Item).filter(
            models.Item.item_code == payload.replaces_item_code.strip().upper()
        ).first()
        if old_item:
            # Decrement completed_kg for the item being replaced (it was previously counted).
            # Only finished (almacén) rolls were counted, so only those get decremented.
            if old_item.current_stage == "almacen" and old_item.status not in ("rejected", "cancelled", "replaced") and old_item.net_weight and old_item.sales_order_line_id:
                replaced_line = db.query(models.SalesOrderLine).filter(
                    models.SalesOrderLine.sales_order_line_id == old_item.sales_order_line_id
                ).first()
                if replaced_line:
                    replaced_line.completed_kg = max(0.0, float(replaced_line.completed_kg or 0) - float(old_item.net_weight))

            item.replaces_item_id         = old_item.item_id
            old_item.replaced_by_item_id  = item.item_id
            old_item.status               = "replaced"
            old_item.cancelled_at         = now
            old_item.cancelled_by_user_id = current_user.user_id
            old_item.cancel_reason        = "reemplazado"
            # Update cancellation record if exists
            existing_cancel = db.query(models.ItemCancellation).filter(
                models.ItemCancellation.item_id == old_item.item_id
            ).first()
            if existing_cancel:
                existing_cancel.replacement_item_id = item.item_id
            else:
                db.add(models.ItemCancellation(
                    item_id              = old_item.item_id,
                    cancelled_by_user_id = current_user.user_id,
                    reason               = "reemplazado",
                    notes                = f"Reemplazado por {item_code}",
                    replacement_item_id  = item.item_id,
                ))

    # Source traceability links
    linked_sources = []
    for src_code in payload.source_item_codes:
        src = db.query(models.Item).filter(
            models.Item.item_code == src_code.strip().upper()
        ).first()
        if src:
            db.add(models.ItemInput(output_item_id=item.item_id, input_item_id=src.item_id))
            linked_sources.append(src_code)

    db.commit()

    return {
        "message":           f"Producido en {payload.station}",
        "item_code":         item.item_code,
        "roll_number":       item.roll_number,
        "barcode_value":     item.barcode_value,
        "gross_weight":      float(item.gross_weight),
        "bobbin_weight_kg":  float(item.bobbin_weight_kg),
        "net_weight":        float(item.net_weight),
        "print_weight_mode": item.print_weight_mode,
        "printed_weight_kg": float(item.printed_weight_kg),
        "branding_mode":     item.branding_mode,
        "current_stage":     item.current_stage,
        "next_stage":        item.next_stage,
        "status":            item.status,
        "shift":             shift,
        "source_items_linked": linked_sources,
        "created_by":        current_user.username,
    }


# ── CANCEL ────────────────────────────────────────────────────────────────

@router.post("/{item_code}/cancel")
def cancel_item(
    item_code:   str,
    payload:     schemas.ItemCancelRequest,
    db:          Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Cancel/void a production item. No supervisor approval required.
    Item stays in history marked ANULADO. Cannot cancel items inside a closed pallet.
    """
    item = db.query(models.Item).filter(models.Item.item_code == item_code).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item no encontrado")

    if item.status in ("cancelled", "replaced"):
        raise HTTPException(status_code=400, detail=f"El item ya está {item.status}")

    # Block if inside a CLOSED pallet
    pallet_entry = db.query(models.PalletItem).filter(
        models.PalletItem.item_id == item.item_id
    ).first()
    if pallet_entry:
        pallet = db.query(models.Pallet).filter(
            models.Pallet.pallet_id == pallet_entry.pallet_id
        ).first()
        if pallet and pallet.status == "closed":
            raise HTTPException(
                status_code=400,
                detail="No se puede cancelar: el rollo ya está en una tarima cerrada."
            )

    previous_status           = item.status
    now                       = datetime.now()
    item.status               = "cancelled"
    item.cancelled_at         = now
    item.cancelled_by_user_id = current_user.user_id
    item.cancel_reason        = payload.reason
    item.cancel_notes         = payload.notes
    if item.active_station_entry_event_id is not None:
        item.active_station_entry_event_id = None
    db.commit()

    cancellation = models.ItemCancellation(
        item_id              = item.item_id,
        cancelled_by_user_id = current_user.user_id,
        reason               = payload.reason,
        notes                = payload.notes,
    )
    db.add(cancellation)
    db.commit()
    db.refresh(cancellation)

    # Decrement completed_kg on the order line.
    # Only if the item was actually counted, i.e. it was a finished (almacén) roll.
    if item.current_stage == "almacen" and previous_status != "rejected" and item.net_weight and item.sales_order_line_id:
        cancel_line = db.query(models.SalesOrderLine).filter(
            models.SalesOrderLine.sales_order_line_id == item.sales_order_line_id
        ).first()
        if cancel_line:
            cancel_line.completed_kg = max(0.0, float(cancel_line.completed_kg or 0) - float(item.net_weight))
            db.commit()

    return {
        "message":           "Item cancelado",
        "item_code":         item.item_code,
        "previous_status":   previous_status,
        "status":            item.status,
        "cancellation_id":   cancellation.cancellation_id,
        "reason":            cancellation.reason,
        "cancelled_by":      current_user.username,
        "cancelled_at":      cancellation.cancelled_at,
    }


# ── GET SINGLE ITEM ───────────────────────────────────────────────────────

@router.get("/{item_code}")
def get_item(
    item_code:   str,
    db:          Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    item = db.query(models.Item).filter(models.Item.item_code == item_code).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item no encontrado")
    return _item_to_dict(item, db)


# ── ITEM HISTORY ─────────────────────────────────────────────────────────

@router.get("/{item_code}/history")
def get_item_history(
    item_code:   str,
    db:          Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    item = db.query(models.Item).filter(models.Item.item_code == item_code).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item no encontrado")

    events = db.query(models.StageEvent).filter(
        models.StageEvent.item_id == item.item_id
    ).order_by(models.StageEvent.event_id.asc()).all()

    prints = db.query(models.LabelPrintEvent).filter(
        models.LabelPrintEvent.item_id == item.item_id
    ).order_by(models.LabelPrintEvent.print_event_id.asc()).all()

    cancellations = db.query(models.ItemCancellation).filter(
        models.ItemCancellation.item_id == item.item_id
    ).order_by(models.ItemCancellation.cancellation_id.asc()).all()

    return {
        "item": _item_to_dict(item, db),
        "events": [
            {
                "event_id":        e.event_id,
                "event_type":      e.event_type,
                "from_stage":      e.from_stage,
                "to_stage":        e.to_stage,
                "machine_id":      e.machine_id,
                "operator_id":     e.operator_id,
                "gross_weight_out": float(e.gross_weight_out) if e.gross_weight_out is not None else None,
                "net_weight_out":  float(e.net_weight_out) if e.net_weight_out is not None else None,
                "tare_constant_kg": float(e.tare_constant_kg) if e.tare_constant_kg is not None else None,
                "shift":           e.shift,
                "event_timestamp": e.event_timestamp,
            }
            for e in events
        ],
        "label_print_events": [
            {
                "print_event_id":  p.print_event_id,
                "print_number":    p.print_number,
                "printed_by_user_id": p.printed_by_user_id,
                "label_type":      p.label_type,
                "branding_mode":   p.branding_mode,
                "print_weight_mode": p.print_weight_mode,
                "printed_weight_kg": float(p.printed_weight_kg) if p.printed_weight_kg else None,
                "is_reprint":      p.is_reprint,
                "reason":          p.reason,
                "printed_at":      p.printed_at,
            }
            for p in prints
        ],
        "cancellations": [
            {
                "cancellation_id":   c.cancellation_id,
                "cancelled_by_user_id": c.cancelled_by_user_id,
                "reason":            c.reason,
                "notes":             c.notes,
                "replacement_item_id": c.replacement_item_id,
                "cancelled_at":      c.cancelled_at,
            }
            for c in cancellations
        ],
    }
