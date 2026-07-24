"""Run a SQL migration file against the configured PostgreSQL database.

Usage:
    python apply_migration.py migrations/002_major_improvements.sql
"""
import sys
from pathlib import Path
from dotenv import load_dotenv
import os
import re
import psycopg2

load_dotenv()

url = os.getenv("DATABASE_URL", "")
m   = re.match(r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", url)
if not m:
    print("ERROR: DATABASE_URL not set or invalid in .env")
    sys.exit(1)

user, pwd, host, port, db = m.groups()

sql_file = sys.argv[1] if len(sys.argv) > 1 else None
if not sql_file or not Path(sql_file).exists():
    print(f"ERROR: SQL file not found: {sql_file}")
    print("Usage: python apply_migration.py <path/to/migration.sql>")
    sys.exit(1)

sql = Path(sql_file).read_text(encoding="utf-8")

conn = psycopg2.connect(dbname=db, user=user, password=pwd, host=host, port=int(port))
conn.autocommit = False
cur  = conn.cursor()

try:
    cur.execute(sql)
    conn.commit()
    print(f"✓ Migration applied: {sql_file}")
except Exception as e:
    conn.rollback()
    print(f"✗ Migration FAILED: {e}")
    raise
finally:
    cur.close()
    conn.close()
