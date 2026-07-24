from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.db import get_db
from app import models, schemas
from app.auth import get_current_user, require_admin

router = APIRouter()


@router.get("/")
def list_orders(db: Session = Depends(get_db)):
    orders = db.query(models.SalesOrder).order_by(models.SalesOrder.sales_order_id.desc()).all()

    result = []
    for so in orders:
        customer = db.query(models.Cliente).filter(models.Cliente.customer_id == so.customer_id).first()
        lot = db.query(models.ProductionLot).filter(models.ProductionLot.sales_order_id == so.sales_order_id).first()
        lines = db.query(models.SalesOrderLine).filter(models.SalesOrderLine.sales_order_id == so.sales_order_id).order_by(models.SalesOrderLine.line_number.asc()).all()

        result.append({
            "sales_order_id": so.sales_order_id,
            "sae_order_number": so.sae_order_number,
            "customer_name": customer.name if customer else None,
            "ordered_kg": float(so.ordered_kg),
            "lot_id": lot.lot_id if lot else None,
            "lot_code": lot.lot_code if lot else None,
            "lines": [
                {
                    "sales_order_line_id": line.sales_order_line_id,
                    "line_number": line.line_number,
                    "product_description": line.product_description,
                    "presentation_type": line.presentation_type,
                    "measure": line.measure,
                    "caliber": line.caliber,
                    "color": line.color,
                    "ordered_kg": float(line.ordered_kg),
                    "completed_kg": float(line.completed_kg),
                    "status": line.status,
                }
                for line in lines
            ]
        })

    return result


@router.get("/by-sae/{sae_order_number}")
def get_order_by_sae(sae_order_number: str, db: Session = Depends(get_db)):
    """Lookup a sales order by its SAE/PI/PAY number.  Used by the operator UI to auto-fill
    customer name and available product lines when the PI/PAY code is entered."""
    so = db.query(models.SalesOrder).filter(models.SalesOrder.sae_order_number == sae_order_number).first()
    if not so:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")

    customer = db.query(models.Cliente).filter(models.Cliente.customer_id == so.customer_id).first()
    lot = db.query(models.ProductionLot).filter(models.ProductionLot.sales_order_id == so.sales_order_id).first()
    lines = (
        db.query(models.SalesOrderLine)
        .filter(models.SalesOrderLine.sales_order_id == so.sales_order_id)
        .order_by(models.SalesOrderLine.line_number.asc())
        .all()
    )

    return {
        "sales_order_id": so.sales_order_id,
        "sae_order_number": so.sae_order_number,
        "customer_name": customer.name if customer else None,
        "ordered_kg": float(so.ordered_kg),
        "lot_id": lot.lot_id if lot else None,
        "lot_code": lot.lot_code if lot else None,
        "customer_default_branding_mode": (customer.default_branding_mode or "normal") if customer else "normal",
        "lines": [
            _line_with_progress(
                db, line,
                customer_default_branding=(customer.default_branding_mode if customer else None),
            )
            for line in lines
        ],
    }


# Statuses that count an item as real warehouse production.
_WAREHOUSE_DEAD_STATUSES = ("cancelled", "replaced", "rejected")


def _line_with_progress(db: Session, line, customer_default_branding: str = None) -> dict:
    """Serialize a sales order line and attach warehouse progress vs CANT.

    Progress = net kg of rolls already sent to 'almacen' (warehouse) compared
    to the CANT column (quantity_ordered). CANT are commercial units, but per
    business decision the operator dashboard compares produced net kg directly
    against that number so they can see roughly how far along the line is.
    """
    warehouse_items = (
        db.query(models.Item)
        .filter(
            models.Item.sales_order_line_id == line.sales_order_line_id,
            models.Item.current_stage == "almacen",
            models.Item.status.notin_(_WAREHOUSE_DEAD_STATUSES),
        )
        .all()
    )
    produced_net_kg = round(sum(float(i.net_weight or 0) for i in warehouse_items), 3)
    roll_count = len(warehouse_items)

    cant = float(line.quantity_ordered) if line.quantity_ordered is not None else None
    pct = round(produced_net_kg / cant * 100, 1) if cant and cant > 0 else None

    # Effective branding default the operator sees pre-checked:
    # a customer flagged as 'distributor' wins; otherwise the line's own default.
    effective_branding = line.default_branding_mode or "normal"
    if customer_default_branding == "distributor":
        effective_branding = "distributor"

    return {
        "sales_order_line_id": line.sales_order_line_id,
        "line_number": line.line_number,
        "product_description": line.product_description,
        "presentation_type": line.presentation_type,
        "measure": line.measure,
        "caliber": line.caliber,
        "color": line.color,
        "ordered_kg": float(line.ordered_kg),
        "completed_kg": float(line.completed_kg),
        "status": line.status,
        "bobbin_weight_kg": float(line.bobbin_weight_kg) if line.bobbin_weight_kg else None,
        "require_bobbin_tare": line.require_bobbin_tare,
        "label_template": line.label_template,
        "default_branding_mode": effective_branding,
        "line_default_branding_mode": line.default_branding_mode or "normal",
        "customer_default_branding_mode": customer_default_branding or "normal",
        "default_print_weight_mode": line.default_print_weight_mode or "gross",
        "default_tare_rule_id": line.default_tare_rule_id,
        # ── Warehouse progress vs CANT ──
        "quantity_ordered": cant,
        "warehouse_net_kg": produced_net_kg,
        "warehouse_roll_count": roll_count,
        "pct_complete": pct,
    }


@router.get("/{sales_order_id}")
def get_order(sales_order_id: int, db: Session = Depends(get_db)):
    so = db.query(models.SalesOrder).filter(models.SalesOrder.sales_order_id == sales_order_id).first()
    if not so:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")

    customer = db.query(models.Cliente).filter(models.Cliente.customer_id == so.customer_id).first()
    lot = db.query(models.ProductionLot).filter(models.ProductionLot.sales_order_id == so.sales_order_id).first()
    lines = db.query(models.SalesOrderLine).filter(models.SalesOrderLine.sales_order_id == so.sales_order_id).order_by(models.SalesOrderLine.line_number.asc()).all()

    return {
        "sales_order_id": so.sales_order_id,
        "sae_order_number": so.sae_order_number,
        "customer_name": customer.name if customer else None,
        "ordered_kg": float(so.ordered_kg),
        "lot_id": lot.lot_id if lot else None,
        "lot_code": lot.lot_code if lot else None,
        "lines": [
            {
                "sales_order_line_id": line.sales_order_line_id,
                "line_number": line.line_number,
                "product_description": line.product_description,
                "presentation_type": line.presentation_type,
                "measure": line.measure,
                "caliber": line.caliber,
                "color": line.color,
                "ordered_kg": float(line.ordered_kg),
                "completed_kg": float(line.completed_kg),
                "status": line.status,
            }
            for line in lines
        ]
    }