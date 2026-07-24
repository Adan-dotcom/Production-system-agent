"""
wipe_data.py — Deja la base en BLANCO para arrancar producción.

Conserva SOLO:
  - el usuario admin (username='admin')
  - las filas de la tabla stations (corte/impresion/bolseo/almacen/reciclaje/extrusion)

Borra todo lo demás (producción, pedidos, Aspel, analytics, operadores de prueba,
máquinas y tare_rules). NO toca el esquema: solo TRUNCATE/DELETE de filas.
Resetea los SERIAL para que items/máquinas empiecen en 1.

Uso:
    .\venv\Scripts\python.exe deploy\wipe_data.py
Para correr sin confirmación (server desatendido):
    .\venv\Scripts\python.exe deploy\wipe_data.py --yes
"""
import os
import re
import sys

from dotenv import load_dotenv
import psycopg2

# Todas las tablas de datos EXCEPTO users y stations.
# El orden no importa: TRUNCATE ... CASCADE resuelve las FK entre ellas.
DATA_TABLES = [
    "machines",
    "clientes",
    "productos",
    "tare_rules",
    "sales_orders",
    "sales_order_lines",
    "aspel_excel_imports",
    "aspel_excel_raw_rows",
    "production_lots",
    "items",
    "stage_events",
    "label_print_events",
    "alerts",
    "event_corrections",
    "pallets",
    "pallet_items",
    "item_inputs",
    "item_cancellations",
    "analytics_insights",
    "analytics_recommendations",
    "agent_runs",
]

ADMIN_USERNAME = "admin"


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

    # Guard: no continuar si no existe el admin que vamos a conservar.
    cur.execute("SELECT user_id FROM users WHERE username = %s AND role = 'admin'", (ADMIN_USERNAME,))
    if not cur.fetchone():
        sys.exit(f"ABORTADO: no existe un usuario admin '{ADMIN_USERNAME}'. No se borra nada.")

    print(f"Base objetivo: {dbname}")
    print("Se CONSERVA: usuario 'admin' + filas de 'stations'.")
    print("Se BORRA: " + ", ".join(DATA_TABLES) + " + usuarios distintos de 'admin'.")

    if not auto_yes:
        resp = input("\n¿Continuar? Esto es IRREVERSIBLE. Escribe BORRAR para confirmar: ").strip()
        if resp != "BORRAR":
            sys.exit("Cancelado. No se tocó nada.")

    tables_sql = ", ".join(DATA_TABLES)
    cur.execute(f"TRUNCATE TABLE {tables_sql} RESTART IDENTITY CASCADE;")
    cur.execute("DELETE FROM users WHERE username <> %s;", (ADMIN_USERNAME,))
    conn.commit()

    # Verificación post-wipe
    cur.execute("SELECT count(*) FROM users")
    n_users = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM stations")
    n_stations = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM items")
    n_items = cur.fetchone()[0]

    print("\n[OK] Wipe completo.")
    print(f"   users restantes:    {n_users} (debe ser 1: admin)")
    print(f"   stations restantes: {n_stations} (debe ser 6)")
    print(f"   items restantes:    {n_items} (debe ser 0)")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
