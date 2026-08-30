"""Apply migrations/003_personalization_weight.sql to the database."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, ".")
from app.db import get_pg_connection  # noqa: E402


def main() -> int:
    sql = Path("migrations/003_personalization_weight.sql").read_text(encoding="utf-8")
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    print("Migration 003 applied.")

    # Verify
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """select column_name from information_schema.columns
                   where table_name='pastoralists' order by ordinal_position"""
            )
            cols = [r["column_name"] for r in cur.fetchall()]
            print("pastoralists columns:", cols)
            for table in ("conversation_state", "weight_records", "herd_estimates"):
                cur.execute(
                    """select count(*) as n from information_schema.tables
                       where table_name = %s""",
                    (table,),
                )
                print(f"{table} exists:", cur.fetchone()["n"] == 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
