from dotenv import load_dotenv
import os, re
import psycopg2

load_dotenv()
url = os.getenv("DATABASE_URL")
m = re.match(r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", url)
user, pwd, host, port, db = m.groups()

conn = psycopg2.connect(dbname=db, user=user, password=pwd, host=host, port=port)
cur = conn.cursor()

cur.execute("ALTER TABLE stage_events ADD COLUMN IF NOT EXISTS shift VARCHAR(20);")

cur.execute("""
CREATE TABLE IF NOT EXISTS item_inputs (
    input_record_id SERIAL PRIMARY KEY,
    output_item_id  INTEGER NOT NULL REFERENCES items(item_id),
    input_item_id   INTEGER NOT NULL REFERENCES items(item_id),
    recorded_at     TIMESTAMP NOT NULL DEFAULT NOW()
);
""")

conn.commit()
cur.close()
conn.close()
print("Migrations OK")
