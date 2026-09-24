"""
Backfill water_sources.indices_as_of from the COG objects in R2.

Why: indices_as_of is supposed to record WHEN the satellite snapshot behind a water
point was taken, but it is only written by the transfer pipeline — so until the next
14-day refresh it was NULL for every point, and anything reading it saw "unknown".
The COG's upload time in R2 is the authoritative answer (that is the file the
advisory reads), so we can set it correctly right now and keep the field honest.

Run: python scripts/backfill_indices_as_of.py            # report + write
     python scripts/backfill_indices_as_of.py --dry-run  # report only
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import timezone

sys.path.insert(0, ".")

from app.config import get_settings  # noqa: E402
from app.db import get_pg_connection  # noqa: E402
from app.services.storage import cog_key, get_s3_client  # noqa: E402

log = logging.getLogger("backfill_indices_as_of")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = get_settings()
    client = get_s3_client()
    updated = missing = 0

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("select id, name, indices_as_of::text as as_of "
                        "from water_sources order by created_at")
            points = cur.fetchall()
            for p in points:
                label = p["name"] or str(p["id"])[:8]
                try:
                    head = client.head_object(Bucket=settings.r2_bucket_name,
                                              Key=cog_key(str(p["id"])))
                except Exception:  # noqa: BLE001
                    missing += 1
                    log.warning("%-30s no COG in R2 - leaving indices_as_of as %s",
                                label, p["as_of"])
                    continue
                taken = head["LastModified"]
                if taken.tzinfo is None:
                    taken = taken.replace(tzinfo=timezone.utc)
                as_of = taken.date().isoformat()
                if p["as_of"] == as_of:
                    log.info("%-30s already %s", label, as_of)
                    continue
                log.info("%-30s %s -> %s (COG uploaded %s)",
                         label, p["as_of"], as_of, taken.isoformat(timespec="seconds"))
                if not args.dry_run:
                    cur.execute(
                        "update water_sources set indices_as_of = %(d)s, updated_at = now() "
                        "where id = %(id)s",
                        {"d": as_of, "id": str(p["id"])},
                    )
                updated += 1
        if not args.dry_run:
            conn.commit()

    log.info("%d updated, %d points without a COG%s",
             updated, missing, " (dry run - nothing written)" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
