import csv
import io

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app import models
from app.auth import require_admin, get_current_user

router = APIRouter()


def get_or_create_customer(db: Session, name: str):
    customer = db.query(models.Cliente).filter(models.Cliente.name == name).first()
    if customer:
        return customer
    customer = models.Cliente(name=name)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def get_or_create_product(db: Session, description: str):
    product = db.query(models.Producto).filter(models.Producto.description == description).first()
    if product:
        return product
    product = models.Producto(description=description, tare_constant_kg=10.0, tolerance_percent=5.0)
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.post("/orders/csv")
async def import_orders_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="El archivo debe ser CSV")

    content = await file.read()
    decoded = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(decoded))

    required_cols = {
        "sae_order_number", "customer_name", "ordered_kg",
        "line_number", "product_description", "line_ordered_kg"
    }
    if not required_cols.issubset(set(reader.fieldnames or [])):
        raise HTTPException(status_code=400, detail=f"Columnas requeridas: {sorted(required_cols)}")

    imported_orders = 0
    imported_lines = 0
    skipped_rows = 0

    seen_orders = {}

    for row in reader:
        sae_order_number = (row.get("sae_order_number") or "").strip()
        customer_name = (row.get("customer_name") or "").strip()
        ordered_kg = float(row.get("ordered_kg") or 0)
        line_number = int(row.get("line_number") or 0)
        product_description = (row.get("product_description") or "").strip()
        line_ordered_kg = float(row.get("line_ordered_kg") or 0)
        lot_code = (row.get("lot_code") or f"LOT-{sae_order_number}").strip()
        presentation_type = (row.get("presentation_type") or "").strip() or None
        measure = (row.get("measure") or "").strip() or None
        caliber = (row.get("caliber") or "").strip() or None
        color = (row.get("color") or "").strip() or None

        if not sae_order_number or not customer_name or ordered_kg <= 0 or line_number <= 0 or not product_description or line_ordered_kg <= 0:
            skipped_rows += 1
            continue

        if sae_order_number in seen_orders:
            so = seen_orders[sae_order_number]
        else:
            so = db.query(models.SalesOrder).filter(models.SalesOrder.sae_order_number == sae_order_number).first()
            if not so:
                customer = get_or_create_customer(db, customer_name)
                product = get_or_create_product(db, product_description)
                so = models.SalesOrder(
                    sae_order_number=sae_order_number,
                    customer_id=customer.customer_id,
                    product_id=product.product_id,
                    ordered_kg=ordered_kg,
                )
                db.add(so)
                db.commit()
                db.refresh(so)
                imported_orders += 1

                existing_lot = db.query(models.ProductionLot).filter(models.ProductionLot.lot_code == lot_code).first()
                if not existing_lot:
                    lot = models.ProductionLot(sales_order_id=so.sales_order_id, lot_code=lot_code)
                    db.add(lot)
                    db.commit()
            seen_orders[sae_order_number] = so

        existing_line = (
            db.query(models.SalesOrderLine)
            .filter(
                models.SalesOrderLine.sales_order_id == so.sales_order_id,
                models.SalesOrderLine.line_number == line_number,
            )
            .first()
        )
        if existing_line:
            skipped_rows += 1
            continue

        line = models.SalesOrderLine(
            sales_order_id=so.sales_order_id,
            line_number=line_number,
            product_description=product_description,
            presentation_type=presentation_type,
            measure=measure,
            caliber=caliber,
            color=color,
            ordered_kg=line_ordered_kg,
            completed_kg=0,
            status="open",
        )
        db.add(line)
        db.commit()
        imported_lines += 1

    return {
        "message": "Importación completada",
        "imported_orders": imported_orders,
        "imported_lines": imported_lines,
        "skipped_rows": skipped_rows,
        "imported_by": current_user.username,
    }