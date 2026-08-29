"""Live-DB migration: add the pastoralists.voice_replies toggle column.

Idempotent — safe to run repeatedly. Uses the raw psycopg connection
(DATABASE_URL) the same way app/db.py does.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv()
from app.db import get_pg_connection  # noqa: E402

SQL = [
    "alter table pastoralists add column if not exists "
    "voice_replies boolean not null default false",
]

with get_pg_connection() as conn:
    with conn.cursor() as cur:
        for stmt in SQL:
            cur.execute(stmt)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns "
            "where table_name = 'pastoralists' and column_name = 'voice_replies'"
        )
        print("voice_replies column present:", cur.fetchone() is not None)
print("Migration applied.")
