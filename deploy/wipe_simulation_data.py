"""
wipe_simulation_data.py — Limpieza DIRIGIDA de datos de producción/simulación.

Borra SOLO la producción (rollos, eventos, etiquetas, tarimas, lotes) y resetea
completed_kg de las líneas a 0. CONSERVA pedidos, líneas, clientes, reglas de tara,
máquinas, estaciones y usuarios (datos maestros / Aspel).

A diferencia de wipe_data.py (que deja la base en blanco total), esto sirve para
limpiar los rollos de prueba sin perder los pedidos importados.

Uso:
    .\venv\Scripts\python.exe deploy\wipe_simulation_data.py            (pide confirmación)
    .\venv\Scripts\python.exe deploy\wipe_simulation_data.py --yes      (sin confirmación)
"""
import os
import re
import sys

from dotenv import load_dotenv
import psycopg2

# Tablas de PRODUCCIÓN a vaciar. TRUNCATE ... CASCADE resuelve las FK entre ellas.
PROD_TABLES = [
    "items",
    "stage_events",
    "label_print_events",
    "pallets",
    "pallet_items",
    "item_inputs",
    "item_cancellations",
    "production_lots",
]


def connect():
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        sys.exit("ERROR: DATABASE_URL no está en .env")
    m = re.match(r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", url)
    if not m:
        sys.exit(f"ERROR: DATABASE_URL con formato inesperado: {url}")
    user, pwd, host, port, db = m.groups()
    return psycopg2.connect(dbname=db, user=user, password=pwd, host=host, port=port), db


def main():
    auto_yes = "--yes" in sys.argv
    conn, dbname = connect()
    cur = conn.cursor()

    print(f"Base objetivo: {dbname}")
    print("Se BORRA (produccion): " + ", ".join(PROD_TABLES))
    print("Se RESETEA: sales_order_lines.completed_kg = 0")
    print("Se CONSERVA: sales_orders, sales_order_lines, clientes, tare_rules, machines, stations, users.")

    if not auto_yes:
        resp = input("\n¿Continuar? Escribe BORRAR para confirmar: ").strip()
        if resp != "BORRAR":
            sys.exit("Cancelado. No se toco nada.")

    cur.execute(f"TRUNCATE TABLE {', '.join(PROD_TABLES)} RESTART IDENTITY CASCADE;")
    cur.execute("UPDATE sales_order_lines SET completed_kg = 0;")
    conn.commit()

    cur.execute("SELECT count(*) FROM items")
    n_items = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM sales_orders")
    n_orders = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM clientes")
    n_cli = cur.fetchone()[0]

    print("\n[OK] Limpieza de simulacion completa.")
    print(f"   items restantes:   {n_items} (debe ser 0)")
    print(f"   pedidos:           {n_orders} (conservados)")
    print(f"   clientes:          {n_cli} (conservados)")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
