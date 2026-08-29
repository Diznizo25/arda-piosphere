"""Live-DB migration: create the water_point_builds tracking table.

Idempotent — safe to run repeatedly. Uses the raw psycopg connection
(DATABASE_URL) the same way app/db.py does.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv()
from app.db import get_pg_connection  # noqa: E402

SQL = Path(__file__).resolve().parent.parent / "migrations" / "002_add_water_point_builds.sql"

with get_pg_connection() as conn:
    with conn.cursor() as cur:
        cur.execute(SQL.read_text())
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(
            "select to_regclass('public.water_point_builds') as tbl"
        )
        print("water_point_builds exists:", cur.fetchone()["tbl"] is not None)
print("Migration applied.")
