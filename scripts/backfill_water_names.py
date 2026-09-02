"""Backfill `name` and `water_type` for existing water_sources from OSM.

The original imports captured only `source_ref` (e.g. "node/1234567"); OSM
nodes/ways carry `name` and type tags we now store at import time. This script
fills the gap for already-imported points so maps can label each water point by
its local name AND what kind it is (river/borehole/well/spring/pan/tap).
Fail-open per point.

Usage:
  python scripts/backfill_water_names.py            # all unnamed/type-less OSM points
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


def osm_tags_to_type(tags: dict) -> str | None:
    """Map OSM tags to our water_type vocabulary."""
    t = tags or {}
    if t.get("waterway") == "river" or t.get("water") == "river":
        return "river"
    if t.get("natural") == "spring":
        return "spring"
    if t.get("man_made") == "water_well":
        return "well"
    if t.get("man_made") == "borehole":
        return "borehole"
    if t.get("man_made") == "water_tap" or t.get("amenity") == "drinking_water":
        return "tap"
    if t.get("water") == "lake" or t.get("landuse") == "reservoir" or t.get("water") == "pond":
        return "pan"
    if t.get("waterway") == "dam":
        return "dam"
    return None


def fetch_details(refs: list[str]) -> dict[str, dict]:
    """refs like \"node/1234\" or \"way/567\" -> {ref: {name, water_type}}."""
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
                headers={"User-Agent": "arda-piosphere/1.0 (details backfill)"},
            )
            resp.raise_for_status()
            out: dict[str, dict] = {}
            for el in resp.json().get("elements", []):
                tags = el.get("tags") or {}
                info: dict = {}
                if tags.get("name"):
                    info["name"] = tags["name"].strip()[:60]
                wt = osm_tags_to_type(tags)
                if wt:
                    info["water_type"] = wt
                if info:
                    out[f"{el['type']}/{el['id']}"] = info
            return out
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("Overpass mirror %s failed: %s", url, e)
    raise RuntimeError(f"all Overpass mirrors failed: {last_err}")


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    # Pinned (herder-registered) points carry their type in the source_ref
    # ("whatsapp:well:2547..."): copy it into the water_type column directly.
    if not dry_run:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """update water_sources
                       set water_type = split_part(source_ref, ':', 2)
                       where source_ref like 'whatsapp:%'
                         and (water_type is null or water_type = '')"""
                )
                log.info("Set water_type for %d pinned (whatsapp) sources.", cur.rowcount)
            conn.commit()
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """select id, source_ref from water_sources
                   where source_ref is not null and (
                       name is null or water_type is null or water_type = ''
                   )
                   order by created_at asc"""
            )
            rows = cur.fetchall()
    if not rows:
        log.info("No water sources to backfill (names/types).")
        return 0
    log.info("Found %d water sources to backfill.", len(rows))
    details = fetch_details([r["source_ref"] for r in rows])
    log.info("Got details for %d OSM refs.", len(details))
    updated = 0
    for r in rows:
        det = details.get(r["source_ref"])
        if not det:
            continue
        name = det.get("name")
        wtype = det.get("water_type")
        if not name and not wtype:
            continue
        if dry_run:
            log.info("  would set %s -> name=%r type=%r", r["id"], name, wtype)
            continue
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """update water_sources
                       set name = coalesce(%(name)s, name),
                           water_type = coalesce(%(water_type)s, water_type)
                       where id = %(id)s""",
                    {"name": name, "water_type": wtype, "id": r["id"]},
                )
            conn.commit()
        updated += 1
        log.info("  %s -> name=%r type=%r", r["source_ref"], name, wtype)
    log.info("Done. %d/%d updated%s.", updated, len(rows), " (dry run)" if dry_run else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
