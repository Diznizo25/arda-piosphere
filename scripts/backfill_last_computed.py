"""
Backfill piosphere_zones.last_computed for every water point whose COG already
exists in R2.

Uses the Supabase REST API (PostgREST over HTTPS) because the raw Postgres
pooler (port 5432) is unreachable from this machine while HTTPS works. Marks
last_computed for each R2 COG's water_source_id (species = 'camel').

Usage:
  python scripts/backfill_last_computed.py
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings  # noqa: E402
from app.db import get_supabase_client  # noqa: E402
from app.services.storage import get_s3_client  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill_last_computed")


def r2_water_source_ids() -> list[str]:
    settings = get_settings()
    client = get_s3_client()
    ids = []
    token = None
    while True:
        kwargs = {"Bucket": settings.r2_bucket_name, "Prefix": "cogs/"}
        if token:
            kwargs["ContinuationToken"] = token
        r = client.list_objects_v2(**kwargs)
        for obj in r.get("Contents", []):
            parts = obj["Key"].split("/")
            if len(parts) == 3 and parts[0] == "cogs" and parts[2] == "indices.tif":
                ids.append(parts[1])
        if r.get("IsTruncated") and r.get("NextContinuationToken"):
            token = r["NextContinuationToken"]
        else:
            break
    return sorted(set(ids))


def main() -> int:
    ids = r2_water_source_ids()
    log.info(f"Found {len(ids)} COGs in R2: {', '.join(i[:8] for i in ids)}")
    if not ids:
        log.warning("Nothing to mark.")
        return 0

    client = get_supabase_client()
    now = datetime.now(timezone.utc).isoformat()
    updated = 0
    failed = 0
    for ws_id in ids:
        try:
            resp = (
                client.table("piosphere_zones")
                .update({"last_computed": now})
                .eq("water_source_id", ws_id)
                .eq("species", "camel")
                .execute()
            )
            count = len(resp.data) if resp and resp.data is not None else 0
            if count:
                updated += 1
                log.info(f"  marked {ws_id[:8]} (rows={count})")
            else:
                failed += 1
                log.warning(f"  no camel row matched for {ws_id[:8]}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            log.warning(f"  failed {ws_id[:8]}: {type(e).__name__}: {e}")

    log.info(f"Done. {updated} marked, {failed} failed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
