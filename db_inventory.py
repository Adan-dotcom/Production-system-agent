"""Inventario de la base de datos: qué tablas tienen datos, rangos de fecha y muestras.
Solo LEE. No modifica nada."""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, inspect

load_dotenv()
engine = create_engine(os.getenv("DATABASE_URL"))

# Tablas con su columna de fecha principal (para ver rango temporal)
DATE_COL = {
    "items": "created_at",
    "stage_events": "event_timestamp",
    "label_print_events": "printed_at",
    "pallets": "created_at",
    "pallet_items": "added_at",
    "sales_orders": "created_at",
    "sales_order_lines": "created_at",
    "production_lots": "created_at",
    "item_cancellations": "cancelled_at",
    "alerts": "created_at",
    "aspel_excel_imports": "started_at",
    "aspel_excel_raw_rows": "created_at",
    "machine_sensor_events": "occurred_at",
    "analytics_insights": "created_at",
    "agent_runs": "started_at",
}

insp = inspect(engine)
tables = sorted(insp.get_table_names())

print("=" * 70)
print("INVENTARIO DE TABLAS")
print("=" * 70)
with engine.connect() as c:
    for t in tables:
        cnt = c.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar()
        rango = ""
        if t in DATE_COL and cnt > 0:
            dc = DATE_COL[t]
            try:
                mn, mx = c.execute(
                    text(f'SELECT MIN("{dc}"), MAX("{dc}") FROM "{t}"')
                ).one()
                if mn:
                    rango = f"   [{str(mn)[:10]} -> {str(mx)[:10]}]"
            except Exception:
                pass
        flag = "  <-- VACIA" if cnt == 0 else ""
        print(f"{t:30s} {cnt:>8} filas{rango}{flag}")
