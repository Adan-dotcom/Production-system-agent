"""
Background scheduler: auto-syncs the Aspel Excel file at a configured interval.
Runs as an asyncio task started in app/main.py lifespan.
"""

import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path

_DOTENV_PATH = Path(__file__).resolve().parent.parent / ".env"

_state: dict = {
    "enabled": False,
    "interval_minutes": 30,
    "last_run_at": None,
    "last_status": None,    # "ok" | "error" | "skipped"
    "last_message": None,
    "next_run_at": None,
}

# Set to wake the scheduler when a manual sync resets the countdown
_manual_trigger = asyncio.Event()


def get_auto_sync_status() -> dict:
    return dict(_state)


def notify_manual_sync(status: str = "ok", message: str = "Sync manual completado") -> None:
    """Call after any manual sync to update state and reset the auto-sync countdown."""
    now = datetime.now()
    interval = _state.get("interval_minutes", 30)
    _state["last_run_at"] = now.strftime("%d/%m/%Y %H:%M:%S")
    _state["last_status"] = status
    _state["last_message"] = message
    if _state.get("enabled"):
        _state["next_run_at"] = (now + timedelta(minutes=interval)).strftime("%d/%m/%Y %H:%M:%S")
    _manual_trigger.set()


def _read_env(key: str, default: str = "") -> str:
    if _DOTENV_PATH.exists():
        for raw in _DOTENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip()
    return os.getenv(key, default).strip()


def _run_one_sync(path_str: str) -> tuple[str, str]:
    """Blocking: read file and call _do_sync with its own DB session."""
    from app.db import SessionLocal
    from app.routes.aspel_sync import _do_sync

    p = Path(path_str)
    if not p.exists():
        return "error", f"Archivo no encontrado: {path_str}"

    try:
        file_bytes = p.read_bytes()
    except PermissionError:
        return "error", f"Sin permiso de lectura: {path_str}"
    except Exception as e:
        return "error", f"Error al leer archivo: {e}"

    db = SessionLocal()
    try:
        result = _do_sync(db, file_bytes, p.name, "auto_scheduler", 1)
        msg = (
            f"{result['imported_orders']} PI nuevos, "
            f"{result['updated_orders']} actualizados, "
            f"{result['imported_lines']} líneas nuevas, "
            f"{result['updated_lines']} actualizadas"
        )
        return "ok", msg
    except Exception as e:
        detail = getattr(e, "detail", str(e))
        status_code = getattr(e, "status_code", None)
        if status_code == 409:
            return "skipped", str(detail)
        return "error", str(detail)
    finally:
        db.close()


async def auto_sync_loop() -> None:
    """Asyncio background task. First run 10 s after boot, then every interval."""
    await asyncio.sleep(10)

    while True:
        # ── Sync phase ────────────────────────────────────────────────────────
        try:
            interval_str = _read_env("ASPEL_SYNC_INTERVAL_MINUTES", "30")
            try:
                interval = max(1, int(interval_str))
            except (ValueError, TypeError):
                interval = 30

            path_str = _read_env("ASPEL_EXCEL_PATH")
            _state["interval_minutes"] = interval

            if path_str:
                _state["enabled"] = True
                status, message = await asyncio.to_thread(_run_one_sync, path_str)
                now = datetime.now()
                _state["last_run_at"] = now.strftime("%d/%m/%Y %H:%M:%S")
                _state["last_status"] = status
                _state["last_message"] = message
                _state["next_run_at"] = (now + timedelta(minutes=interval)).strftime("%d/%m/%Y %H:%M:%S")
            else:
                _state["enabled"] = False
                _state["next_run_at"] = None

        except asyncio.CancelledError:
            raise
        except Exception as e:
            _state["last_status"] = "error"
            _state["last_message"] = f"Error inesperado en scheduler: {e}"

        # ── Sleep phase ───────────────────────────────────────────────────────
        # Inner loop: sleep until the natural interval elapses.
        # If a manual sync fires the trigger mid-sleep, just reset the countdown
        # and sleep again — do NOT loop back to sync (the endpoint already synced).
        sleep_secs = _state["interval_minutes"] * 60 if _state["enabled"] else 60
        while True:
            try:
                await asyncio.wait_for(_manual_trigger.wait(), timeout=sleep_secs)
                _manual_trigger.clear()
                # Manual sync already ran via the endpoint — restart the sleep window
                sleep_secs = _state["interval_minutes"] * 60 if _state["enabled"] else 60
            except asyncio.TimeoutError:
                break  # Natural interval elapsed → exit inner loop → sync
