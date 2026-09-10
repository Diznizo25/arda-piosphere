"""Apply migrations/009_water_status.sql to the database."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, ".")
from app.db import get_pg_connection  # noqa: E402


def main() -> int:
    sql = Path("migrations/009_water_status.sql").read_text(encoding="utf-8")
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    print("Migration 009 applied.")

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """select column_name from information_schema.columns
                   where table_name = 'water_sources'
                     and column_name in ('status','status_updated_at','status_source',
                                         'status_reports')
                   order by column_name"""
            )
            cols = [r["column_name"] for r in cur.fetchall()]
            print("water_sources status columns:", cols)
            cur.execute(
                """select column_name from information_schema.columns
                   where table_name = 'pastoralists'
                     and column_name in ('last_advisory_water_source_id','last_advisory_at')
                   order by column_name"""
            )
            print("pastoralists advisory columns:", [r["column_name"] for r in cur.fetchall()])
            cur.execute("select status, count(*) as n from water_sources group by status")
            print("status counts:", {r["status"]: r["n"] for r in cur.fetchall()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
