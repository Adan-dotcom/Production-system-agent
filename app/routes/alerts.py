from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app import models
from app.auth import get_current_user, require_admin

router = APIRouter()


@router.get("/")
def list_alerts(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    alerts = db.query(models.Alert).order_by(models.Alert.alert_id.desc()).all()

    result = []
    for alert in alerts:
        item = db.query(models.Item).filter(models.Item.item_id == alert.item_id).first()
        result.append({
            "alert_id": alert.alert_id,
            "item_id": alert.item_id,
            "item_code": item.item_code if item else None,
            "event_id": alert.event_id,
            "alert_type": alert.alert_type,
            "severity": alert.severity,
            "message": alert.message,
            "created_at": alert.created_at,
            "resolved": alert.resolved,
            "resolved_by_user_id": alert.resolved_by_user_id,
            "resolved_at": alert.resolved_at,
        })
    return result


@router.post("/{alert_id}/resolve")
def resolve_alert(alert_id: int, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    alert = db.query(models.Alert).filter(models.Alert.alert_id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alerta no encontrada")

    alert.resolved = True
    alert.resolved_by_user_id = current_user.user_id
    alert.resolved_at = datetime.now()
    db.commit()

    return {
        "message": "Alerta resuelta",
        "alert_id": alert.alert_id,
        "resolved": alert.resolved,
        "resolved_by_user_id": alert.resolved_by_user_id,
        "resolved_at": alert.resolved_at,
        "resolved_by_username": current_user.username,
    }