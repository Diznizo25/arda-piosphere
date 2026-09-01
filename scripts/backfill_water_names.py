"""Backfill `name` for existing water_sources from OSM (via Overpass).

The original imports captured only `source_ref` (e.g. "node/1234567"); OSM
nodes/ways carry a `name` tag we now store at import time. This script fills the
gap for already-imported points so the confirmation list shows local names
instead of wards. Fail-open per point.

Usage:
  python scripts/backfill_water_names.py            # all unnamed OSM points
  python scripts/backfill_water_names.py --dry-run
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.db import get_pg_connection  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill_water_names")

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]


def fetch_names(refs: list[str]) -> dict[str, str]:
    """refs like "node/1234" or "way/567" -> {ref: name}."""
    nodes = [r.split("/")[1] for r in refs if r.startswith("node/")]
    ways = [r.split("/")[1] for r in refs if r.startswith("way/")]
    parts = []
    if nodes:
        parts.append(f"node(id:{','.join(nodes)});")
    if ways:
        parts.append(f"way(id:{','.join(ways)});")
    if not parts:
        return {}
    query = "[out:json][timeout:60]; (" + "".join(parts) + "); out tags;"
    last_err: Exception | None = None
    for url in OVERPASS_URLS:
        try:
            resp = httpx.post(
                url,
                data={"data": query},
                timeout=120,
                headers={"User-Agent": "arda-piosphere/1.0 (name backfill)"},
            )
            resp.raise_for_status()
            out: dict[str, str] = {}
            for el in resp.json().get("elements", []):
                name = (el.get("tags") or {}).get("name")
                if name:
                    out[f"{el['type']}/{el['id']}"] = name.strip()[:60]
            return out
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("Overpass mirror %s failed: %s", url, e)
    raise RuntimeError(f"all Overpass mirrors failed: {last_err}")


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """select id, source_ref from water_sources
                   where name is null and source_ref is not null
                   order by created_at asc"""
            )
            rows = cur.fetchall()
    if not rows:
        log.info("No unnamed water sources to backfill.")
        return 0
    log.info("Found %d unnamed water sources.", len(rows))
    names = fetch_names([r["source_ref"] for r in rows])
    log.info("Got %d names from OSM.", len(names))
    updated = 0
    for r in rows:
        name = names.get(r["source_ref"])
        if not name:
            continue
        if dry_run:
            log.info("  would set %s -> %r", r["id"], name)
            continue
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "update water_sources set name = %(name)s where id = %(id)s",
                    {"name": name, "id": r["id"]},
                )
            conn.commit()
        updated += 1
        log.info("  %s -> %r", r["source_ref"], name)
    log.info("Done. %d/%d updated%s.", updated, len(rows), " (dry run)" if dry_run else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
