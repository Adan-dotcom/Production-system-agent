"""Reset admin password to 'admin1234' for local testing."""
import psycopg2, bcrypt

conn = psycopg2.connect(
    dbname='biotecnica_mvp', user='postgres', password='adansito',
    host='localhost', port=5432
)
cur = conn.cursor()

new_pw = 'admin1234'
hashed = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()

cur.execute(
    "UPDATE users SET password_hash=%s WHERE username='admin'",
    (hashed,)
)
conn.commit()
print(f"✓ admin password reset to: {new_pw}")
cur.close(); conn.close()
