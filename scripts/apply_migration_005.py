"""Apply migrations/005_herder_water_point.sql to the database."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, ".")
from app.db import get_pg_connection  # noqa: E402


def main() -> int:
    sql = Path("migrations/005_herder_water_point.sql").read_text(encoding="utf-8")
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    print("Migration 005 applied.")

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """select count(*) as n from information_schema.columns
                   where table_name = 'pastoralists' and column_name = 'water_source_id'"""
            )
            print("pastoralists.water_source_id exists:", cur.fetchone()["n"] == 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
