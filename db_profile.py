"""Perfila las tablas que SI tienen datos: muestra columnas con % de llenado y filas de ejemplo.
Solo LEE."""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
engine = create_engine(os.getenv("DATABASE_URL"))

TABLAS = ["sales_orders", "sales_order_lines", "clientes",
          "machines", "stations", "machine_sensor_events", "aspel_excel_imports"]

def fill_rates(c, t):
    cols = [r[0] for r in c.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name=:t ORDER BY ordinal_position"), {"t": t})]
    total = c.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar()
    print(f"\n{'='*70}\nTABLA: {t}  ({total} filas)\n{'='*70}")
    for col in cols:
        nn = c.execute(text(f'SELECT COUNT("{col}") FROM "{t}"')).scalar()
        pct = (nn / total * 100) if total else 0
        marca = "" if pct > 0 else "   (siempre vacia)"
        print(f"  {col:28s} {pct:5.0f}% lleno{marca}")

def sample(c, t, n=3):
    rows = c.execute(text(f'SELECT * FROM "{t}" LIMIT {n}')).mappings().all()
    print(f"  --- {n} filas de ejemplo ---")
    for r in rows:
        d = {k: v for k, v in r.items() if v not in (None, "")}
        print("   ", d)

with engine.connect() as c:
    for t in TABLAS:
        fill_rates(c, t)
        sample(c, t)
