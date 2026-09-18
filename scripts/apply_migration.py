"""Apply a migration SQL file, then show the resulting schema.

Usage:
  python scripts/apply_migration.py migrations/010_environment.sql
  python scripts/apply_migration.py --check 010          # verify only, no apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.db import get_pg_connection  # noqa: E402

CHECKS = {
    "009": [
        ("column", "water_sources", "status"),
        ("column", "water_sources", "status_updated_at"),
        ("column", "pastoralists", "last_advisory_water_source_id"),
    ],
    "010": [
        ("column", "water_sources", "indices_as_of"),
        ("table", "rainfall_climatology", None),
        ("table", "environment_daily", None),
        ("table", "environment_forecast", None),
    ],
}


def verify(tag: str) -> None:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            for kind, name, column in CHECKS.get(tag, []):
                if kind == "column":
                    cur.execute(
                        """select 1 from information_schema.columns
                           where table_name = %(t)s and column_name = %(c)s""",
                        {"t": name, "c": column},
                    )
                    print(f"  column {name}.{column}: {'OK' if cur.fetchone() else 'MISSING'}")
                else:
                    cur.execute(
                        "select 1 from information_schema.tables where table_name = %(t)s",
                        {"t": name},
                    )
                    print(f"  table  {name}: {'OK' if cur.fetchone() else 'MISSING'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", help="path to the migration .sql")
    parser.add_argument("--check", help="only verify a migration tag (e.g. 010)")
    args = parser.parse_args()

    if args.check:
        print(f"verifying migration {args.check}")
        verify(args.check)
        return 0

    if not args.file:
        parser.error("provide a migration file or --check <tag>")
    path = Path(args.file)
    sql = path.read_text(encoding="utf-8")
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    print(f"{path.name} applied.")
    tag = path.name.split("_", 1)[0]
    if tag in CHECKS:
        verify(tag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
