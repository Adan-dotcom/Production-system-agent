"""
MQTT subscriber for the machine on/off monitor.

Each ESP32 reads a Hall sensor on a roller (1 neodymium magnet → 1 pulse/rev),
computes the pulse frequency of the last second, and publishes it to:

    {MQTT_TOPIC_PREFIX}/{machine_id}/pulso      payload: {"freq_hz": 12.5}

The server subscribes, upserts machine_sensor_status, and logs a
machine_sensor_event only when the running state flips (start/stop).
The girando/parado/sin_señal inference for the dashboard happens at READ time
in app/routes/monitor.py (so thresholds stay tunable without rewriting data).

Implementation note: we use paho-mqtt's own background thread (loop_start),
NOT an asyncio integration. On Windows uvicorn runs the Proactor event loop,
which does not implement add_reader/add_writer — async MQTT clients crash there.
paho's threaded loop is event-loop agnostic and reconnects on its own.

Started/stopped from app/main.py lifespan. If MQTT_BROKER_HOST is unset the
monitor is disabled and the server runs normally.
"""
import json
import os
from datetime import datetime


def _ingest_pulse(machine_id: int, freq_hz: float) -> None:
    """Upsert live status + log start/stop transitions. Runs in paho's thread."""
    from app.db import SessionLocal
    from app import models

    db = SessionLocal()
    try:
        # FK requires a real machine; ignore unknown machine_ids silently.
        if db.get(models.Machine, machine_id) is None:
            return

        now = datetime.utcnow()
        running_now = freq_hz > 0
        status = db.get(models.MachineSensorStatus, machine_id)

        if status is None:
            status = models.MachineSensorStatus(
                machine_id=machine_id,
                last_freq_hz=freq_hz,
                is_running=running_now,
                last_pulse_at=now,
                updated_at=now,
            )
            db.add(status)
            if running_now:  # first time we ever see it and it's already turning
                db.add(models.MachineSensorEvent(
                    machine_id=machine_id, event_type="start", freq_hz=freq_hz, occurred_at=now))
        else:
            if running_now != status.is_running:
                db.add(models.MachineSensorEvent(
                    machine_id=machine_id,
                    event_type="start" if running_now else "stop",
                    freq_hz=freq_hz,
                    occurred_at=now,
                ))
            status.is_running = running_now
            status.last_freq_hz = freq_hz
            status.last_pulse_at = now
            status.updated_at = now

        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _parse_machine_id(prefix: str, topic: str) -> int | None:
    """biotecnica/maquinas/7/pulso → 7"""
    rest = topic[len(prefix):].strip("/").split("/")
    if rest and rest[0].isdigit():
        return int(rest[0])
    return None


def _on_connect(client, userdata, flags, reason_code, properties=None):
    topic = f"{userdata['prefix']}/+/pulso"
    client.subscribe(topic)
    print(f"[mqtt_monitor] conectado al broker, suscrito a {topic}")


def _on_message(client, userdata, msg):
    machine_id = _parse_machine_id(userdata["prefix"], msg.topic)
    if machine_id is None:
        return
    try:
        data = json.loads(msg.payload.decode("utf-8"))
        freq_hz = float(data.get("freq_hz", 0))
    except (ValueError, AttributeError, json.JSONDecodeError):
        return
    if freq_hz < 0:
        freq_hz = 0.0
    _ingest_pulse(machine_id, freq_hz)


def start_mqtt_monitor():
    """Connect + start paho's background thread. Returns the client (or None if disabled)."""
    host = os.getenv("MQTT_BROKER_HOST", "").strip()
    if not host:
        return None  # monitor disabled — server runs normally without a broker

    port = int(os.getenv("MQTT_BROKER_PORT", "1883"))
    prefix = os.getenv("MQTT_TOPIC_PREFIX", "biotecnica/maquinas").strip().rstrip("/")

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("[mqtt_monitor] paho-mqtt no instalado — monitor MQTT deshabilitado. "
              "Ejecuta: pip install paho-mqtt")
        return None

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, userdata={"prefix": prefix})
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)  # auto-retry if broker is down
    client.connect_async(host, port, keepalive=30)
    client.loop_start()
    print(f"[mqtt_monitor] iniciado contra {host}:{port} (prefix '{prefix}')")
    return client


def stop_mqtt_monitor(client) -> None:
    if client is not None:
        client.loop_stop()
        client.disconnect()
