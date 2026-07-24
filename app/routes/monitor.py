"""
Machine on/off monitor API (Hall sensor → ESP32 → MQTT → server).

Ingest is handled out-of-band by the MQTT subscriber (app/services/mqtt_monitor.py),
so there is NO public HTTP ingest endpoint. These endpoints only READ, and the
girando/parado/sin_señal state is INFERRED here at read time:

  GET /monitor/status                 live state of every sensorized machine
  GET /monitor/machines/{id}/history  last start/stop transitions of a machine

Thresholds (seconds) are read from .env so they stay tunable:
  MACHINE_STALE_SECONDS  no message in this long  → 'sin_senal' (ESP/red caída)
"""
import os
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app import models
from app.auth import get_current_user

router = APIRouter()


def _stale_seconds() -> float:
    try:
        return float(os.getenv("MACHINE_STALE_SECONDS", "6"))
    except (ValueError, TypeError):
        return 6.0


@router.get("/status")
def get_status(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """State of every machine that has a sensor reporting (one card each in /ui)."""
    stale = _stale_seconds()
    now = datetime.utcnow()

    rows = (
        db.query(models.MachineSensorStatus, models.Machine, models.Station)
        .join(models.Machine, models.Machine.machine_id == models.MachineSensorStatus.machine_id)
        .join(models.Station, models.Station.station_id == models.Machine.station_id)
        .order_by(models.Station.name, models.Machine.machine_name)
        .all()
    )

    # When did each machine last flip start↔stop? Lets the UI alert on a machine
    # that has been NOT running for a while (vs a 1-second blip).
    last_change = dict(
        db.query(models.MachineSensorEvent.machine_id,
                 func.max(models.MachineSensorEvent.occurred_at))
        .group_by(models.MachineSensorEvent.machine_id)
        .all()
    )

    machines = []
    for status, machine, station in rows:
        secs = (now - status.last_pulse_at).total_seconds() if status.last_pulse_at else None
        online = secs is not None and secs <= stale

        if not online:
            state = "sin_senal"          # ESP o red caída — sin datos recientes
        elif status.is_running:
            state = "girando"
        else:
            state = "parado"             # ESP vivo reportando freq=0

        magnets = status.magnets_per_rev or 1
        freq = float(status.last_freq_hz or 0)
        rpm = round(freq * 60 / magnets, 1) if state == "girando" else 0.0

        lc = last_change.get(machine.machine_id)
        state_since_seconds = round((now - lc).total_seconds(), 1) if lc else None

        machines.append({
            "machine_id": machine.machine_id,
            "machine_code": machine.machine_code,
            "machine_name": machine.machine_name,
            "station": station.name,
            "state": state,
            "online": online,
            "last_freq_hz": freq,
            "rpm": rpm,
            "seconds_since_pulse": round(secs, 1) if secs is not None else None,
            "state_since_seconds": state_since_seconds,
        })

    return {
        "stale_seconds": stale,
        "server_time_utc": now.isoformat(),
        "machines": machines,
    }


@router.get("/machines/{machine_id}/history")
def get_history(machine_id: int, limit: int = Query(100, ge=1, le=500),
                db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Last start/stop transitions for a machine (most recent first)."""
    events = (
        db.query(models.MachineSensorEvent)
        .filter(models.MachineSensorEvent.machine_id == machine_id)
        .order_by(models.MachineSensorEvent.occurred_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "machine_id": machine_id,
        "events": [
            {
                "event_type": e.event_type,
                "freq_hz": float(e.freq_hz or 0),
                "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
            }
            for e in events
        ],
    }
