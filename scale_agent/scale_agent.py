"""
scale_agent.py — Agente local de báscula para Biotécnica MVP
Corre en cada PC de estación en http://127.0.0.1:8787
Lee puerto COM/serial y expone peso vivo via REST.

NO tiene relación con el backend central ni con JWT.
"""

import json
import re
import threading
import time
from pathlib import Path
from typing import Optional

import serial
import serial.tools.list_ports
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

# ── Archivos de configuración ─────────────────────────────────────────────────
CONFIG_FILE = Path(__file__).parent / "scale_config.json"

DEFAULT_CONFIG = {
    "port": None,
    "baudrate": 9600,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "timeout": 1,
    "read_mode": "continuous",
    "command": None,
    "unit": "kg",
    "stability_window": 5,
    "stability_tolerance_kg": 0.02,
    "reconnect_seconds": 2,
}

# ── Aplicación FastAPI ────────────────────────────────────────────────────────
app = FastAPI(title="Scale Agent", version="1.0.0")

# Chrome Private Network Access: necesario cuando operator.html viene de
# http://IP_SERVIDOR:8000 y llama a http://127.0.0.1:8787 (red privada).
# Este header debe ir en la respuesta al preflight OPTIONS y en todas las demás.
class PrivateNetworkAccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response

# Orden de add_middleware: el último agregado es el más externo (envuelve al resto).
# CORS se agrega primero (interno) para que maneje los preflights;
# PNA se agrega después (externo) para inyectar el header en TODAS las respuestas,
# incluyendo las de CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_private_network=True,   # responde correctamente al preflight PNA de Chrome
)
app.add_middleware(PrivateNetworkAccessMiddleware)  # belt+suspenders: header en todas las respuestas

# ── Estado global (protegido por _lock) ──────────────────────────────────────
_lock = threading.Lock()
_state = {
    "connected":       False,
    "port":            None,
    "baudrate":        9600,
    "error":           None,
    "weight_kg":       None,
    "stable":          None,
    "raw":             None,
    "last_read_ts":    None,
    "recent_weights":  [],
    "simulation":      False,   # True cuando el peso viene de /debug/simulate-weight
}
_config: dict = dict(DEFAULT_CONFIG)
_serial_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


# ── Config helpers ────────────────────────────────────────────────────────────
def load_config() -> None:
    global _config
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
            _config = {**DEFAULT_CONFIG, **data}
        except Exception:
            _config = dict(DEFAULT_CONFIG)
    else:
        _config = dict(DEFAULT_CONFIG)


def save_config() -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(_config, f, indent=2)
    except Exception as exc:
        print(f"[scale] Warning: no se pudo guardar config: {exc}")


# ── Parsing serial ────────────────────────────────────────────────────────────
def detect_stability_hint(line: str) -> Optional[bool]:
    """Detecta tokens de estabilidad en la línea raw."""
    tokens = set(re.split(r"[,\s]+", line.upper()))
    if tokens & {"ST", "STABLE"}:
        return True
    if tokens & {"US", "UNSTABLE", "MOTION"}:
        return False
    return None


def parse_weight(line: str) -> tuple:
    """
    Parsea número y unidad de la línea serial.
    Retorna (weight_kg: float|None, stable_hint: bool|None).

    Formatos soportados:
      "ST,GS,+0012.345 kg"   "US,GS,+0012.345 kg"
      "ST,NT, 12.345kg"      "+12.345 kg"
      "-0.002 kg"            "12.345"
      "PESO: 12.345 kg"      "001234 g"
      "27.20 lb"             "=00012.3(kg)"   (báscula Biotécnica COM6)
    """
    line = line.strip()
    if not line:
        return None, None

    stable_hint = detect_stability_hint(line)

    # Extrae número + unidad opcional
    # Acepta coma decimal (europeo) y unidad entre paréntesis: "=00012.3(kg)"
    pattern = r"([+-]?\s*\d+(?:[.,]\d+)?)\s*\(?\s*(kg|g|lbs?)?\s*\)?"
    matches = re.findall(pattern, line, re.IGNORECASE)

    for num_str, unit_str in matches:
        num_str = num_str.replace(" ", "").replace(",", ".")
        try:
            value = float(num_str)
        except ValueError:
            continue

        unit_str = (unit_str or "").lower().strip()
        if not unit_str:
            unit_str = _config.get("unit", "kg")

        if unit_str == "g":
            weight_kg = value / 1000.0
        elif unit_str in ("lb", "lbs"):
            weight_kg = value * 0.45359237
        else:
            weight_kg = value  # kg o sin unidad → asumir kg

        return weight_kg, stable_hint

    return None, stable_hint


def compute_stability(
    recent: list,
    stable_hint: Optional[bool],
    tolerance: float,
    window: int,
) -> Optional[bool]:
    """Calcula estabilidad. Prioriza hint del protocolo; si no, calcula."""
    if stable_hint is not None:
        return stable_hint
    if len(recent) < window:
        return None
    span = max(recent[-window:]) - min(recent[-window:])
    return span <= tolerance


# ── Thread serial ─────────────────────────────────────────────────────────────
def serial_reader() -> None:
    """Background thread que lee el puerto COM en loop."""
    while not _stop_event.is_set():
        port = _config.get("port")

        if not port:
            with _lock:
                # Si hay una simulación activa, no pisamos el estado —
                # el operador inyectó un peso de prueba deliberadamente.
                if not _state.get("simulation"):
                    _state["connected"] = False
                    _state["port"] = None
                    _state["error"] = "No hay puerto configurado"
            _stop_event.wait(timeout=float(_config.get("reconnect_seconds", 2)))
            continue

        ser = None
        try:
            ser = serial.Serial(
                port=port,
                baudrate=int(_config.get("baudrate", 9600)),
                bytesize=int(_config.get("bytesize", 8)),
                parity=str(_config.get("parity", "N")),
                stopbits=int(_config.get("stopbits", 1)),
                timeout=float(_config.get("timeout", 1)),
            )

            with _lock:
                _state["connected"] = True
                _state["port"] = port
                _state["baudrate"] = int(_config.get("baudrate", 9600))
                _state["error"] = None
                _state["recent_weights"] = []
                _state["simulation"] = False   # conexión real → limpiar simulación

            print(f"[scale] Conectado a {port} @ {_config.get('baudrate')} bps")

            while not _stop_event.is_set():
                try:
                    raw = ser.readline()
                    if not raw:
                        continue
                    line = raw.decode("ascii", errors="replace").strip()
                    if not line:
                        continue

                    weight_kg, stable_hint = parse_weight(line)

                    with _lock:
                        _state["raw"] = line
                        _state["last_read_ts"] = time.time()
                        if weight_kg is not None:
                            _state["weight_kg"] = weight_kg
                            recent = _state["recent_weights"]
                            recent.append(weight_kg)
                            window = int(_config.get("stability_window", 5))
                            # Mantener buffer razonable
                            if len(recent) > window * 3:
                                _state["recent_weights"] = recent[-(window * 2):]
                            tolerance = float(_config.get("stability_tolerance_kg", 0.02))
                            _state["stable"] = compute_stability(
                                _state["recent_weights"], stable_hint, tolerance, window
                            )

                except serial.SerialException as exc:
                    with _lock:
                        _state["connected"] = False
                        _state["error"] = str(exc)
                    print(f"[scale] Error leyendo {port}: {exc}")
                    break

        except serial.SerialException as exc:
            err_str = str(exc)
            if "Access is denied" in err_str or "PermissionError" in err_str:
                friendly = f"Access denied / port busy: {port}"
            elif "FileNotFoundError" in err_str or "could not open port" in err_str.lower():
                friendly = f"Puerto no encontrado: {port}"
            else:
                friendly = err_str

            with _lock:
                _state["connected"] = False
                _state["port"] = port
                _state["baudrate"] = int(_config.get("baudrate", 9600))
                _state["error"] = friendly

            print(f"[scale] {friendly}")

        finally:
            if ser is not None:
                try:
                    if ser.is_open:
                        ser.close()
                except Exception:
                    pass

        reconnect = float(_config.get("reconnect_seconds", 2))
        print(f"[scale] Reintentando en {reconnect}s…")
        _stop_event.wait(timeout=reconnect)


def _start_thread() -> None:
    global _serial_thread
    _stop_event.clear()
    _serial_thread = threading.Thread(target=serial_reader, daemon=True, name="scale-reader")
    _serial_thread.start()


# ── Startup ───────────────────────────────────────────────────────────────────
@app.on_event("startup")
def startup() -> None:
    load_config()
    _start_thread()
    print(f"[scale] Agente iniciado. Puerto configurado: {_config.get('port') or 'ninguno'}")


# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/health")
def health() -> dict:
    with _lock:
        return {
            "ok":        True,
            "service":   "scale_agent",
            "connected": _state["connected"],
            "port":      _state["port"],
            "baudrate":  _state["baudrate"],
            "error":     _state["error"],
        }


@app.get("/ports")
def list_ports() -> list:
    return [
        {
            "device":      p.device,
            "description": p.description,
            "hwid":        p.hwid,
        }
        for p in serial.tools.list_ports.comports()
    ]


@app.get("/weight")
def get_weight() -> dict:
    with _lock:
        ts = _state["last_read_ts"]
        age_ms = int((time.time() - ts) * 1000) if ts else None
        return {
            "ok":        True,
            "connected": _state["connected"],
            "weight_kg": _state["weight_kg"],
            "stable":    _state["stable"],
            "raw":       _state["raw"],
            "port":      _state["port"],
            "baudrate":  _state["baudrate"],
            "age_ms":    age_ms,
            "error":     _state["error"],
            "simulation": _state["simulation"],
        }


class ConfigUpdate(BaseModel):
    port:                  Optional[str]   = None
    baudrate:              Optional[int]   = None
    bytesize:              Optional[int]   = None
    parity:                Optional[str]   = None
    stopbits:              Optional[int]   = None
    timeout:               Optional[float] = None
    read_mode:             Optional[str]   = None
    command:               Optional[str]   = None
    unit:                  Optional[str]   = None
    stability_window:      Optional[int]   = None
    stability_tolerance_kg: Optional[float] = None
    reconnect_seconds:     Optional[float] = None


class SimulateWeightBody(BaseModel):
    weight_kg: float
    stable:    Optional[bool] = True
    raw:       Optional[str]  = None


@app.post("/debug/simulate-weight")
def simulate_weight(body: SimulateWeightBody) -> dict:
    """
    Inyecta una lectura simulada en el estado interno.
    Solo para pruebas sin báscula física. Disponible únicamente en loopback.

    Ejemplo:
      curl -X POST http://127.0.0.1:8787/debug/simulate-weight \\
           -H "Content-Type: application/json" \\
           -d '{"weight_kg": 12.345, "stable": true, "raw": "SIM,ST,+0012.345 kg"}'
    """
    raw_str = body.raw or f"SIM,{'ST' if body.stable else 'US'},+{body.weight_kg:010.3f} kg"
    with _lock:
        _state["connected"]    = True
        _state["simulation"]   = True
        _state["weight_kg"]    = body.weight_kg
        _state["stable"]       = body.stable
        _state["raw"]          = raw_str
        _state["last_read_ts"] = time.time()
        _state["error"]        = None
        # Agregar al historial de estabilidad también
        recent = _state["recent_weights"]
        recent.append(body.weight_kg)
        if len(recent) > 20:
            _state["recent_weights"] = recent[-20:]

    print(f"[scale] SIMULADO: {body.weight_kg:.3f} kg  stable={body.stable}  raw={raw_str!r}")
    with _lock:
        return {
            "ok":        True,
            "simulation": True,
            "weight_kg": _state["weight_kg"],
            "stable":    _state["stable"],
            "raw":       _state["raw"],
        }


@app.post("/config")
def update_config(cfg: ConfigUpdate) -> dict:
    global _serial_thread

    # Mezclar con config existente.
    # exclude_unset=True → solo incluye campos que el cliente envió explícitamente,
    # lo que permite pasar "port": null para limpiar el puerto (a diferencia de omitir el campo).
    update = cfg.dict(exclude_unset=True)
    _config.update(update)
    save_config()

    # Detener thread anterior
    _stop_event.set()
    if _serial_thread and _serial_thread.is_alive():
        _serial_thread.join(timeout=5)

    # Resetear estado (incluye limpiar modo simulación)
    with _lock:
        _state.update({
            "connected":      False,
            "weight_kg":      None,
            "stable":         None,
            "raw":            None,
            "last_read_ts":   None,
            "recent_weights": [],
            "error":          None,
            "simulation":     False,
        })

    # Reiniciar con nueva config
    _start_thread()

    with _lock:
        return {
            "ok":       True,
            "config":   dict(_config),
            "connected": _state["connected"],
            "error":    _state["error"],
        }
