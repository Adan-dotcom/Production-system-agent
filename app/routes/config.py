from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app import models, schemas
from app.auth import get_current_user, require_admin

router = APIRouter()


# ── STATIONS ──────────────────────────────────────────────────────────────

@router.get("/stations")
def list_stations(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    stations = db.query(models.Station).order_by(models.Station.station_id.asc()).all()
    return [
        {"station_id": s.station_id, "code": s.code, "name": s.name, "is_active": s.is_active}
        for s in stations
    ]


# ── MACHINES ──────────────────────────────────────────────────────────────

@router.get("/machines")
def list_machines(
    station:     str  = None,
    include_all: bool = False,
    db:          Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    q = db.query(models.Machine)
    if not include_all:
        q = q.filter(models.Machine.is_active == True)
    if station:
        st = db.query(models.Station).filter(models.Station.code == station).first()
        if st:
            q = q.filter(models.Machine.station_id == st.station_id)
    machines = q.order_by(models.Machine.machine_id.asc()).all()
    stations_map = {s.station_id: s.code for s in db.query(models.Station).all()}
    return [
        {
            "machine_id":   m.machine_id,
            "station_id":   m.station_id,
            "station_code": stations_map.get(m.station_id, ""),
            "machine_code": m.machine_code,
            "machine_name": m.machine_name,
            "is_active":    m.is_active,
        }
        for m in machines
    ]


@router.post("/machines")
def create_machine(
    payload:     schemas.MachineCreateRequest,
    db:          Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    station = db.query(models.Station).filter(models.Station.station_id == payload.station_id).first()
    if not station:
        raise HTTPException(status_code=404, detail="Estación no encontrada")

    existing = db.query(models.Machine).filter(models.Machine.machine_code == payload.machine_code).first()
    if existing:
        raise HTTPException(status_code=400, detail="machine_code ya existe")

    machine = models.Machine(
        station_id   = payload.station_id,
        machine_code = payload.machine_code,
        machine_name = payload.machine_name,
        is_active    = True,
    )
    db.add(machine)
    db.commit()
    db.refresh(machine)

    return {
        "message":     "Máquina creada",
        "machine_id":  machine.machine_id,
        "machine_code": machine.machine_code,
        "machine_name": machine.machine_name,
    }


@router.patch("/machines/{machine_id}")
def update_machine(
    machine_id:  int,
    payload:     schemas.MachineUpdateRequest,
    db:          Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    machine = db.query(models.Machine).filter(models.Machine.machine_id == machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Máquina no encontrada")

    if payload.machine_name is not None:
        machine.machine_name = payload.machine_name
    if payload.station_id is not None:
        station = db.query(models.Station).filter(models.Station.station_id == payload.station_id).first()
        if not station:
            raise HTTPException(status_code=404, detail="Estación no encontrada")
        machine.station_id = payload.station_id
    if payload.is_active is not None:
        machine.is_active = payload.is_active

    db.commit()
    return {
        "message":     "Máquina actualizada",
        "machine_id":  machine.machine_id,
        "machine_name": machine.machine_name,
        "is_active":   machine.is_active,
    }


# ── TARE RULES ────────────────────────────────────────────────────────────

@router.get("/tare-rules")
def list_tare_rules(
    include_inactive: bool    = False,
    db:               Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    q = db.query(models.TareRule)
    if not include_inactive:
        q = q.filter(models.TareRule.is_active == True)
    rules = q.order_by(models.TareRule.id.asc()).all()
    return [_tare_rule_dict(r) for r in rules]


@router.post("/tare-rules")
def create_tare_rule(
    payload:     schemas.TareRuleCreateRequest,
    db:          Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    if payload.rule_type not in ("fixed", "linear_density", "threshold", "manual"):
        raise HTTPException(status_code=400, detail="rule_type inválido")

    if payload.rule_type == "fixed" and payload.fixed_weight_kg is None:
        raise HTTPException(status_code=400, detail="fixed_weight_kg requerido para regla tipo 'fixed'")

    # If new rule is default, unset current default
    if payload.is_default:
        db.query(models.TareRule).filter(models.TareRule.is_default == True).update({"is_default": False})

    rule = models.TareRule(
        name                    = payload.name,
        rule_type               = payload.rule_type,
        fixed_weight_kg         = payload.fixed_weight_kg,
        linear_density_kg_per_m = payload.linear_density_kg_per_m,
        default_length_m        = payload.default_length_m,
        threshold_field         = payload.threshold_field,
        threshold_min           = payload.threshold_min,
        threshold_max           = payload.threshold_max,
        product_code            = payload.product_code,
        station_code            = payload.station_code,
        is_default              = payload.is_default,
        is_active               = True,
        notes                   = payload.notes,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)

    return {"message": "Regla de tara creada", **_tare_rule_dict(rule)}


@router.patch("/tare-rules/{rule_id}")
def update_tare_rule(
    rule_id:     int,
    payload:     schemas.TareRuleUpdateRequest,
    db:          Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    rule = db.query(models.TareRule).filter(models.TareRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Regla no encontrada")

    if payload.name is not None:
        rule.name = payload.name
    if payload.rule_type is not None:
        rule.rule_type = payload.rule_type
    if payload.fixed_weight_kg is not None:
        rule.fixed_weight_kg = payload.fixed_weight_kg
    if payload.linear_density_kg_per_m is not None:
        rule.linear_density_kg_per_m = payload.linear_density_kg_per_m
    if payload.default_length_m is not None:
        rule.default_length_m = payload.default_length_m
    if payload.notes is not None:
        rule.notes = payload.notes
    if payload.is_active is not None:
        rule.is_active = payload.is_active
    if payload.is_default is not None:
        if payload.is_default:
            db.query(models.TareRule).filter(models.TareRule.is_default == True).update({"is_default": False})
        rule.is_default = payload.is_default

    rule.updated_at = datetime.now()
    db.commit()

    return {"message": "Regla actualizada", **_tare_rule_dict(rule)}


@router.delete("/tare-rules/{rule_id}")
def deactivate_tare_rule(
    rule_id:     int,
    db:          Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    rule = db.query(models.TareRule).filter(models.TareRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Regla no encontrada")
    if rule.is_default:
        raise HTTPException(status_code=400, detail="No se puede desactivar la regla default. Primero establece otra como default.")
    rule.is_active  = False
    rule.updated_at = datetime.now()
    db.commit()
    return {"message": "Regla desactivada", "id": rule_id}


def _tare_rule_dict(rule: models.TareRule) -> dict:
    return {
        "id":                      rule.id,
        "name":                    rule.name,
        "rule_type":               rule.rule_type,
        "fixed_weight_kg":         float(rule.fixed_weight_kg) if rule.fixed_weight_kg is not None else None,
        "linear_density_kg_per_m": float(rule.linear_density_kg_per_m) if rule.linear_density_kg_per_m is not None else None,
        "default_length_m":        float(rule.default_length_m) if rule.default_length_m is not None else None,
        "threshold_field":         rule.threshold_field,
        "threshold_min":           float(rule.threshold_min) if rule.threshold_min is not None else None,
        "threshold_max":           float(rule.threshold_max) if rule.threshold_max is not None else None,
        "product_code":            rule.product_code,
        "station_code":            rule.station_code,
        "is_default":              rule.is_default,
        "is_active":               rule.is_active,
        "notes":                   rule.notes,
        "created_at":              rule.created_at.isoformat() if rule.created_at else None,
        "updated_at":              rule.updated_at.isoformat() if rule.updated_at else None,
    }


# ── CUSTOMERS (branding por default) ───────────────────────────────────────

@router.get("/customers")
def list_customers(
    search:           str  = None,
    only_distributor: bool = False,
    db:               Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Lista de clientes para que el admin designe cuáles llevan etiqueta de
    distribuidor por default. Soporta búsqueda por nombre y conteo de pedidos."""
    q = db.query(models.Cliente)
    if search:
        q = q.filter(models.Cliente.name.ilike(f"%{search.strip()}%"))
    if only_distributor:
        q = q.filter(models.Cliente.default_branding_mode == "distributor")
    customers = q.order_by(models.Cliente.name.asc()).all()

    # order counts in one query
    from sqlalchemy import func as _func
    counts = dict(
        db.query(models.SalesOrder.customer_id, _func.count(models.SalesOrder.sales_order_id))
        .group_by(models.SalesOrder.customer_id)
        .all()
    )
    return [
        {
            "customer_id":           c.customer_id,
            "name":                  c.name,
            "default_branding_mode": c.default_branding_mode or "normal",
            "is_distributor":        (c.default_branding_mode == "distributor"),
            "order_count":           int(counts.get(c.customer_id, 0)),
        }
        for c in customers
    ]


@router.patch("/customers/{customer_id}")
def update_customer_branding(
    customer_id: int,
    payload:     schemas.CustomerBrandingUpdateRequest,
    db:          Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Marca/desmarca a un cliente como distribuidor por default. El operador verá
    la casilla 'Distribuidor' pre-seleccionada en las líneas de este cliente."""
    if payload.default_branding_mode not in ("normal", "distributor"):
        raise HTTPException(status_code=400, detail="default_branding_mode inválido")

    customer = db.query(models.Cliente).filter(models.Cliente.customer_id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    customer.default_branding_mode = payload.default_branding_mode
    db.commit()

    return {
        "message":               "Cliente actualizado",
        "customer_id":           customer.customer_id,
        "name":                  customer.name,
        "default_branding_mode": customer.default_branding_mode,
    }
