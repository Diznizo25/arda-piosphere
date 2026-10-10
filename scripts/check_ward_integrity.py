"""Is any of our water-point data in the wrong place? One command, no writes.

Checks every row of `water_sources` against the ward polygons we ship:

  * a row whose stored ward disagrees with the ward its coordinates fall in
  * a row whose coordinates fall in no ward we serve (a boundary pin, another county,
    or a polygon we are missing)
  * a row with no coordinates, or no ward at all
  * orphaned raster caches in data/tiles (points that no longer exist in the database)

Exit code is non-zero when anything needs a human look, so it can be wired into a
deploy check later. It NEVER deletes or updates anything: removing herder-reported data
is a reviewed decision, not a cron job.

Usage:
    python scripts/check_ward_integrity.py            # report, exit 0/1
    python scripts/check_ward_integrity.py --quiet     # only problems
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.db import get_pg_connection  # noqa: E402
from app.services import wards  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quiet", action="store_true", help="print only problems")
    args = ap.parse_args()

    with get_pg_connection() as conn:
        rows = conn.execute("""
            select id, name, ward, county, source_type,
                   st_y(geom) as lat, st_x(geom) as lon
            from water_sources order by ward nulls first, name nulls first""").fetchall()

        by_ward = conn.execute("""
            select coalesce(ward, '(none)') as ward, count(*) as n
            from water_sources group by 1 order by 2 desc""").fetchall()

    print(f"water_sources rows: {len(rows)}")
    for r in by_ward:
        print(f"  ward {r['ward']:<14} {r['n']:>4}")
    print(f"wards we ship: {', '.join(wards.known_wards())}\n")

    problems: list[str] = []
    for r in rows:
        sid, ward = str(r["id"]), (r["ward"] or "").strip()
        name = str(r["name"] or "-")
        if r["lat"] is None or r["lon"] is None:
            problems.append(f"{sid[:8]} {name!r}: no coordinates — cannot place it")
            continue
        hit = wards.ward_for(float(r["lat"]), float(r["lon"]))
        if not ward:
            problems.append(f"{sid[:8]} {name!r}: no ward stored (coords are in "
                            f"{hit.ward or 'no ward we ship'})")
            continue
        if hit.ward is None:
            problems.append(f"{sid[:8]} {name!r}: stored as {ward!r}, but its coordinates "
                            f"are outside every ward we ship")
            continue
        if ward.lower() != hit.ward.lower():
            problems.append(f"{sid[:8]} {name!r}: stored as {ward!r}, coordinates are in "
                            f"{hit.ward!r}")
        elif not args.quiet:
            print(f"  ok  {sid[:8]} {name[:32]:<32} {ward}")

    # Orphaned local caches: points that no longer exist (or were never imported).
    tiles = Path("data/tiles")
    if tiles.exists():
        db_ids = {str(r["id"]) for r in rows}
        orphans = [d.name for d in sorted(tiles.iterdir())
                   if d.is_dir() and d.name not in db_ids]
        if orphans:
            problems.append(f"{len(orphans)} local raster cache(s) with no database row: "
                            f"{', '.join(o[:8] for o in orphans)}")
        elif not args.quiet:
            print(f"  ok  local raster caches: {len(db_ids)} dirs, all matched to rows")

    if problems:
        print(f"\n{len(problems)} thing(s) to look at:")
        for p in problems:
            print(f"  ! {p}")
        print("\nNothing was changed. Removing a herder's point is a reviewed decision: "
              "dump the rows first, then delete deliberately.")
        return 1
    print("\nward integrity OK — every point is filed under the ward it sits in")
    return 0


if __name__ == "__main__":
    sys.exit(main())
