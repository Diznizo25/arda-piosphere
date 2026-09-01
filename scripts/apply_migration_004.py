"""Apply migrations/004_dashboard_query_log.sql to the database."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, ".")
from app.db import get_pg_connection  # noqa: E402


def main() -> int:
    sql = Path("migrations/004_dashboard_query_log.sql").read_text(encoding="utf-8")
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    print("Migration 004 applied.")

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """select count(*) as n from information_schema.tables
                   where table_name = 'query_log'"""
            )
            print("query_log exists:", cur.fetchone()["n"] == 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
