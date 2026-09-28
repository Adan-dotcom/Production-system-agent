from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app import models, schemas
from app.auth import require_admin, hash_password

router = APIRouter()


@router.get("/users")
def list_users(db: Session = Depends(get_db), current_user=Depends(require_admin)):
    users = db.query(models.User).order_by(models.User.user_id.asc()).all()
    return [
        {
            "user_id": u.user_id,
            "username": u.username,
            "full_name": u.full_name,
            "role": u.role,
            "is_active": u.is_active,
            "created_at": u.created_at,
        }
        for u in users
    ]


@router.post("/users")
def create_user(payload: schemas.UserCreateRequest, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    if payload.role not in ["admin", "operator"]:
        raise HTTPException(status_code=400, detail="Role inválido")

    existing = db.query(models.User).filter(models.User.username == payload.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username ya existe")

    user = models.User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "message": "Usuario creado",
        "user_id": user.user_id,
        "username": user.username,
        "full_name": user.full_name,
        "role": user.role,
        "is_active": user.is_active,
    }


@router.patch("/users/{user_id}/status")
def update_user_status(user_id: int, payload: schemas.UserUpdateStatusRequest, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    user = db.query(models.User).filter(models.User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    user.is_active = payload.is_active
    db.commit()

    return {"message": "Estado actualizado", "user_id": user.user_id, "is_active": user.is_active}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    """Borra un usuario PARA SIEMPRE (ej. renunció). Distinto de desactivar
    (que es para ausencias temporales como vacaciones, y solo lo oculta del
    dropdown sin perder nada).

    Un borrado real no es posible sin perder trazabilidad si el usuario ya
    produjo/imprimió/canceló algo — esas tablas tienen su user_id como FK NOT
    NULL (quién hizo qué rollo, quién imprimió, quién canceló). Por eso aquí
    se bloquea con un mensaje claro en vez de fallar feo por la FK, o peor,
    en vez de borrar en cascada el historial de producción. Si tiene
    historial, la única opción real es desactivarlo.
    """
    user = db.query(models.User).filter(models.User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.user_id == current_user.user_id:
        raise HTTPException(status_code=400, detail="No puedes borrar tu propio usuario")

    history_checks = [
        ("rollos producidos",  db.query(models.Item).filter(models.Item.created_by_user_id == user_id).count()),
        ("rollos cancelados",  db.query(models.Item).filter(models.Item.cancelled_by_user_id == user_id).count()),
        ("eventos de etapa",   db.query(models.StageEvent).filter(models.StageEvent.operator_id == user_id).count()),
        ("impresiones",        db.query(models.LabelPrintEvent).filter(models.LabelPrintEvent.printed_by_user_id == user_id).count()),
        ("tarimas creadas",    db.query(models.Pallet).filter(models.Pallet.created_by_user_id == user_id).count()),
        ("tarimas cerradas",   db.query(models.Pallet).filter(models.Pallet.closed_by_user_id == user_id).count()),
        ("correcciones",       db.query(models.EventCorrection).filter(models.EventCorrection.corrected_by_user_id == user_id).count()),
        ("cancelaciones",      db.query(models.ItemCancellation).filter(models.ItemCancellation.cancelled_by_user_id == user_id).count()),
        ("rollos en tarimas",  db.query(models.PalletItem).filter(models.PalletItem.added_by_user_id == user_id).count()),
        ("alertas resueltas",  db.query(models.Alert).filter(models.Alert.resolved_by_user_id == user_id).count()),
        ("syncs de Excel",     db.query(models.AspelExcelImport).filter(models.AspelExcelImport.triggered_by == user_id).count()),
    ]
    total_history = sum(count for _, count in history_checks)
    if total_history > 0:
        detalle = ", ".join(f"{count} {label}" for label, count in history_checks if count > 0)
        raise HTTPException(
            status_code=400,
            detail=f"No se puede borrar: este usuario tiene historial de producción ({detalle}). "
                   f"Borrarlo perdería la trazabilidad de esos registros. Usa 'Desactivar' en su lugar.",
        )

    db.delete(user)
    db.commit()
    return {"message": "Usuario borrado permanentemente", "user_id": user_id}


@router.patch("/users/{user_id}/password")
def reset_user_password(user_id: int, payload: schemas.UserResetPasswordRequest, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    user = db.query(models.User).filter(models.User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    user.password_hash = hash_password(payload.new_password)
    db.commit()

    return {"message": "Password actualizado", "user_id": user.user_id}


@router.post("/corrections")
def create_correction(payload: schemas.CorrectionCreateRequest, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    event = db.query(models.StageEvent).filter(models.StageEvent.event_id == payload.target_event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Evento no encontrado")

    allowed_fields = {"gross_weight_in", "net_weight_in", "gross_weight_out", "net_weight_out", "discrepancy_note"}
    if payload.field_name not in allowed_fields:
        raise HTTPException(status_code=400, detail="field_name inválido")

    old_value = getattr(event, payload.field_name)
    setattr(event, payload.field_name, payload.new_value)

    # recálculo básico si corrigieron pesos brutos y hay tare
    tare = float(event.tare_constant_kg or 0)
    if payload.field_name == "gross_weight_in":
        try:
            event.net_weight_in = float(payload.new_value) - tare
        except Exception:
            pass
    elif payload.field_name == "gross_weight_out":
        try:
            event.net_weight_out = float(payload.new_value) - tare
        except Exception:
            pass

    if event.net_weight_in is not None and event.net_weight_out is not None:
        try:
            event.shrinkage_kg = float(event.net_weight_in) - float(event.net_weight_out)
        except Exception:
            pass

    correction = models.EventCorrection(
        item_id=event.item_id,
        target_event_id=event.event_id,
        corrected_by_user_id=current_user.user_id,
        field_name=payload.field_name,
        old_value=str(old_value) if old_value is not None else None,
        new_value=str(payload.new_value),
        reason=payload.reason,
    )

    db.add(correction)
    db.commit()
    db.refresh(correction)

    return {
        "message": "Corrección registrada",
        "correction_id": correction.correction_id,
        "target_event_id": correction.target_event_id,
        "field_name": correction.field_name,
        "old_value": correction.old_value,
        "new_value": correction.new_value,
        "reason": correction.reason,
    }

