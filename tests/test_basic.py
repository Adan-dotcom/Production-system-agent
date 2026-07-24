"""
Minimal validation tests for director-corrections backlog.
Run with: pytest tests/test_basic.py -v

Requires a running PostgreSQL instance (uses the DATABASE_URL from .env)
or set DATABASE_URL to a test database before running.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db import Base, get_db
from app import models
from app.auth import hash_password

# ── Test database ────────────────────────────────────────────────────────────
import os
from dotenv import load_dotenv

load_dotenv()

TEST_DB_URL = os.getenv("TEST_DATABASE_URL", os.getenv("DATABASE_URL"))

test_engine = create_engine(TEST_DB_URL)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(scope="session", autouse=True)
def setup_tables():
    Base.metadata.create_all(bind=test_engine)
    yield
    # Do not drop tables — preserve data between runs


@pytest.fixture()
def db():
    session = TestSessionLocal()
    yield session
    session.close()


@pytest.fixture()
def admin_token(db):
    user = db.query(models.User).filter(models.User.username == "test_admin").first()
    if not user:
        user = models.User(
            username="test_admin",
            password_hash=hash_password("testpass"),
            full_name="Test Admin",
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    resp = client.post("/auth/login", json={"username": "test_admin", "password": "testpass"})
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest.fixture()
def operator_user(db):
    user = db.query(models.User).filter(models.User.username == "test_operator").first()
    if not user:
        user = models.User(
            username="test_operator",
            password_hash=hash_password("oppass"),
            full_name="Test Operador",
            role="operator",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


# ── 1. PI/PAY dropdown — /orders/by-sae/{sae} ──────────────────────────────

def test_pay_lookup_not_found(admin_token):
    resp = client.get(
        "/orders/by-sae/NOEXISTE-999",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


def test_pay_lookup_returns_lines(admin_token, db):
    customer = db.query(models.Cliente).filter(models.Cliente.name == "Cliente Test PI").first()
    if not customer:
        customer = models.Cliente(name="Cliente Test PI")
        db.add(customer)
        db.commit()
        db.refresh(customer)

    producto = db.query(models.Producto).first()
    if not producto:
        producto = models.Producto(description="Bolsa Test", tare_constant_kg=5.0, tolerance_percent=5.0)
        db.add(producto)
        db.commit()
        db.refresh(producto)

    so = db.query(models.SalesOrder).filter(models.SalesOrder.sae_order_number == "PI-TEST-001").first()
    if not so:
        so = models.SalesOrder(
            sae_order_number="PI-TEST-001",
            customer_id=customer.customer_id,
            product_id=producto.product_id,
            ordered_kg=500,
        )
        db.add(so)
        db.commit()
        db.refresh(so)

        line = models.SalesOrderLine(
            sales_order_id=so.sales_order_id,
            line_number=1,
            product_description="Bolsa 60x90 calibre 100",
            measure="60x90",
            caliber="100",
            ordered_kg=500,
        )
        db.add(line)
        db.commit()

    resp = client.get(
        "/orders/by-sae/PI-TEST-001",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["sae_order_number"] == "PI-TEST-001"
    assert data["customer_name"] == "Cliente Test PI"
    assert len(data["lines"]) >= 1
    line_data = data["lines"][0]
    # Operator must not manually type product — it comes from the line
    assert "product_description" in line_data
    assert "measure" in line_data
    assert "caliber" in line_data


# ── 2. No reception stage ────────────────────────────────────────────────────

def test_no_reception_stage_in_valid_stages():
    """Reception ('recepcion') must not be a valid production stage."""
    from app.routes.items import VALID_STAGES
    assert "recepcion" not in VALID_STAGES
    assert "recepción" not in VALID_STAGES


# ── 3. Operator list / select-operator ──────────────────────────────────────

def test_operator_list_endpoint(operator_user):
    resp = client.get("/auth/operators")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    usernames = [u["username"] for u in data]
    assert "test_operator" in usernames


def test_select_operator_returns_token(operator_user):
    resp = client.post("/auth/select-operator", json={"user_id": operator_user.user_id})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["user"]["role"] == "operator"


def test_select_operator_rejects_admin(admin_token, db):
    admin = db.query(models.User).filter(models.User.username == "test_admin").first()
    resp = client.post("/auth/select-operator", json={"user_id": admin.user_id})
    assert resp.status_code == 403


# ── 4. Reprint logging ───────────────────────────────────────────────────────

def test_reprint_logged_with_higher_print_number(admin_token, db):
    """Second print call on same item must create a LabelPrintEvent with print_number > 1."""
    # Create minimal item
    station = db.query(models.Station).first()
    if not station:
        station = models.Station(code="EXT", name="Extrusión")
        db.add(station)
        db.commit()
        db.refresh(station)

    machine = db.query(models.Machine).first()
    if not machine:
        machine = models.Machine(station_id=station.station_id, machine_code="M1-TEST", machine_name="Máquina 1 Test", is_active=True)
        db.add(machine)
        db.commit()
        db.refresh(machine)

    customer = db.query(models.Cliente).filter(models.Cliente.name == "Cliente Reprint").first()
    if not customer:
        customer = models.Cliente(name="Cliente Reprint")
        db.add(customer)
        db.commit()
        db.refresh(customer)

    producto = db.query(models.Producto).first()

    so = db.query(models.SalesOrder).filter(models.SalesOrder.sae_order_number == "PI-REPRINT-001").first()
    if not so:
        so = models.SalesOrder(sae_order_number="PI-REPRINT-001", customer_id=customer.customer_id, product_id=producto.product_id, ordered_kg=100)
        db.add(so)
        db.commit()
        db.refresh(so)

    lot = db.query(models.ProductionLot).filter(models.ProductionLot.lot_code == "LOT-REPRINT-001").first()
    if not lot:
        lot = models.ProductionLot(sales_order_id=so.sales_order_id, lot_code="LOT-REPRINT-001")
        db.add(lot)
        db.commit()
        db.refresh(lot)

    line = db.query(models.SalesOrderLine).filter(models.SalesOrderLine.sales_order_id == so.sales_order_id).first()
    if not line:
        line = models.SalesOrderLine(sales_order_id=so.sales_order_id, line_number=1, product_description="Bolsa Reprint", ordered_kg=100)
        db.add(line)
        db.commit()
        db.refresh(line)

    # Create item via corte
    resp = client.post(
        "/items/corte",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "lot_id": lot.lot_id,
            "sales_order_line_id": line.sales_order_line_id,
            "gross_weight": 60.0,
            "machine_id": machine.machine_id,
            "next_stage": "impresion",
        },
    )
    assert resp.status_code == 200
    item_code = resp.json()["item_code"]

    # First print
    resp1 = client.post(
        f"/labels/{item_code}/print",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"template": "internal", "reason": "Impresión inicial"},
    )
    assert resp1.status_code == 200
    assert resp1.json()["print_number"] == 1
    assert resp1.json()["label_type"] == "PRINT"

    # Reprint (same item)
    resp2 = client.post(
        f"/labels/{item_code}/print",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"template": "internal", "reason": "Etiqueta se despegó mal"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["print_number"] == 2
    assert resp2.json()["label_type"] == "REPRINT"


# ── 5. Cancellation logging ──────────────────────────────────────────────────

def test_cancellation_logged_and_item_not_deleted(admin_token, db):
    customer = db.query(models.Cliente).filter(models.Cliente.name == "Cliente Cancel").first()
    if not customer:
        customer = models.Cliente(name="Cliente Cancel")
        db.add(customer)
        db.commit()
        db.refresh(customer)

    producto = db.query(models.Producto).first()

    so = db.query(models.SalesOrder).filter(models.SalesOrder.sae_order_number == "PI-CANCEL-001").first()
    if not so:
        so = models.SalesOrder(sae_order_number="PI-CANCEL-001", customer_id=customer.customer_id, product_id=producto.product_id, ordered_kg=100)
        db.add(so)
        db.commit()
        db.refresh(so)

    lot = db.query(models.ProductionLot).filter(models.ProductionLot.lot_code == "LOT-CANCEL-001").first()
    if not lot:
        lot = models.ProductionLot(sales_order_id=so.sales_order_id, lot_code="LOT-CANCEL-001")
        db.add(lot)
        db.commit()
        db.refresh(lot)

    line = db.query(models.SalesOrderLine).filter(models.SalesOrderLine.sales_order_id == so.sales_order_id).first()
    if not line:
        line = models.SalesOrderLine(sales_order_id=so.sales_order_id, line_number=1, product_description="Bolsa Cancel", ordered_kg=100)
        db.add(line)
        db.commit()
        db.refresh(line)

    machine = db.query(models.Machine).first()
    resp = client.post(
        "/items/corte",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"lot_id": lot.lot_id, "sales_order_line_id": line.sales_order_line_id, "gross_weight": 55.0, "machine_id": machine.machine_id if machine else None, "next_stage": "impresion"},
    )
    assert resp.status_code == 200
    item_code = resp.json()["item_code"]

    # Cancel the item
    resp_cancel = client.post(
        f"/items/{item_code}/cancel",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"reason": "Producto incorrecto seleccionado", "notes": "El operador puso la medida equivocada"},
    )
    assert resp_cancel.status_code == 200
    data = resp_cancel.json()
    assert data["status"] == "cancelled"
    assert data["reason"] == "Producto incorrecto seleccionado"

    # Item must still exist in DB (not deleted)
    resp_get = client.get(f"/items/{item_code}", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp_get.status_code == 200
    assert resp_get.json()["status"] == "cancelled"


# ── 6. Gross/net weight calculation ─────────────────────────────────────────

def test_net_weight_equals_gross_minus_tare(admin_token, db):
    producto = db.query(models.Producto).first()
    tare = float(producto.tare_constant_kg)

    customer = db.query(models.Cliente).filter(models.Cliente.name == "Cliente Tare").first()
    if not customer:
        customer = models.Cliente(name="Cliente Tare")
        db.add(customer)
        db.commit()
        db.refresh(customer)

    so = db.query(models.SalesOrder).filter(models.SalesOrder.sae_order_number == "PI-TARE-001").first()
    if not so:
        so = models.SalesOrder(sae_order_number="PI-TARE-001", customer_id=customer.customer_id, product_id=producto.product_id, ordered_kg=200)
        db.add(so)
        db.commit()
        db.refresh(so)

    lot = db.query(models.ProductionLot).filter(models.ProductionLot.lot_code == "LOT-TARE-001").first()
    if not lot:
        lot = models.ProductionLot(sales_order_id=so.sales_order_id, lot_code="LOT-TARE-001")
        db.add(lot)
        db.commit()
        db.refresh(lot)

    line = db.query(models.SalesOrderLine).filter(models.SalesOrderLine.sales_order_id == so.sales_order_id).first()
    if not line:
        line = models.SalesOrderLine(sales_order_id=so.sales_order_id, line_number=1, product_description="Bolsa Tare", ordered_kg=200)
        db.add(line)
        db.commit()
        db.refresh(line)

    gross = 80.0
    machine = db.query(models.Machine).first()
    resp = client.post(
        "/items/corte",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"lot_id": lot.lot_id, "sales_order_line_id": line.sales_order_line_id, "gross_weight": gross, "machine_id": machine.machine_id if machine else None, "next_stage": "impresion"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert abs(data["net_weight"] - (gross - tare)) < 0.001


def test_bobbin_tare_overrides_product_tare(admin_token, db):
    """When require_bobbin_tare is True on the order line, that weight overrides the product tare."""
    producto = db.query(models.Producto).first()
    bobbin_weight = 3.5

    customer = db.query(models.Cliente).filter(models.Cliente.name == "Cliente Bobbin").first()
    if not customer:
        customer = models.Cliente(name="Cliente Bobbin")
        db.add(customer)
        db.commit()
        db.refresh(customer)

    so = db.query(models.SalesOrder).filter(models.SalesOrder.sae_order_number == "PI-BOBBIN-001").first()
    if not so:
        so = models.SalesOrder(sae_order_number="PI-BOBBIN-001", customer_id=customer.customer_id, product_id=producto.product_id, ordered_kg=200)
        db.add(so)
        db.commit()
        db.refresh(so)

    lot = db.query(models.ProductionLot).filter(models.ProductionLot.lot_code == "LOT-BOBBIN-001").first()
    if not lot:
        lot = models.ProductionLot(sales_order_id=so.sales_order_id, lot_code="LOT-BOBBIN-001")
        db.add(lot)
        db.commit()
        db.refresh(lot)

    line = db.query(models.SalesOrderLine).filter(
        models.SalesOrderLine.sales_order_id == so.sales_order_id,
        models.SalesOrderLine.line_number == 1,
    ).first()
    if not line:
        line = models.SalesOrderLine(
            sales_order_id=so.sales_order_id,
            line_number=1,
            product_description="Bolsa Bobbin",
            ordered_kg=200,
            bobbin_weight_kg=bobbin_weight,
            require_bobbin_tare=True,
        )
        db.add(line)
        db.commit()
        db.refresh(line)

    gross = 50.0
    machine = db.query(models.Machine).first()
    resp = client.post(
        "/items/corte",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"lot_id": lot.lot_id, "sales_order_line_id": line.sales_order_line_id, "gross_weight": gross, "machine_id": machine.machine_id if machine else None, "next_stage": "impresion"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert abs(data["net_weight"] - (gross - bobbin_weight)) < 0.001


# ── 7. Report export endpoint ────────────────────────────────────────────────

def test_report_export_csv_returns_content(admin_token):
    resp = client.get(
        "/reports/export/csv",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    lines = resp.text.strip().split("\n")
    # Must have header row
    assert "item_code" in lines[0]


def test_report_export_filters_by_stage(admin_token):
    resp = client.get(
        "/reports/export/csv?stage=corte",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200


def test_report_summary_json(admin_token):
    resp = client.get(
        "/reports/summary",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "total_items" in data
    assert "total_gross_kg" in data
    assert "total_reprints" in data
    assert "total_cancelled" in data
