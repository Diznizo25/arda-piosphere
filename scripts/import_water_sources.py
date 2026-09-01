"""
Import water points into `water_sources`, scoped to a single admin boundary
(ward or county). Parameterized from the start so the exact same script runs
for one-ward validation (days 1-4) and full-Isiolo scale-up (days 6-7) — only
the --boundary argument changes.

Sources:
  - OSM, via Overpass API, polygon-filtered to the boundary
  - WPDx (Water Point Data Exchange), via the public WPDx API, filtered to
    Kenya then clipped to the boundary polygon

Usage:
  python scripts/import_water_sources.py \
      --boundary config/wards/isiolo_ward_example.geojson \
      --ward "Oldonyiro" \
      --county "Isiolo" \
      --source both

Requires: DATABASE_URL or SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY in .env.

NOTE: this script needs an actual ward-boundary GeoJSON (Polygon/MultiPolygon,
EPSG:4326) to run. We don't ship Isiolo ward boundaries in this repo yet —
see config/wards/README.md for where to get them (IEBC/HDX Kenya admin
boundaries). Pass the county boundary instead for the full-Isiolo run.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

import httpx
from shapely.geometry import shape, Point, Polygon, MultiPolygon
from shapely.ops import unary_union
from tenacity import retry, stop_after_attempt, wait_exponential

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings  # noqa: E402
from app.db import get_supabase_client  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("import_water_sources")

# overpass-api.de is frequently overloaded (504/406). Use the private.coffee
# mirror as the primary endpoint — it is more reliable for batch imports.
OVERPASS_URL = "https://overpass.private.coffee/api/interpreter"
OVERPASS_FALLBACK_URL = "https://overpass-api.de/api/interpreter"
WPDX_API_URL = "https://data.waterpointdata.org/resource/eqje-vguj.json"  # WPDx-Core Socrata endpoint

SourceType = Literal["osm", "wpdx"]


@dataclass
class WaterPointRecord:
    lon: float
    lat: float
    source_type: SourceType
    source_ref: str | None
    confidence: float
    name: str | None = None


def load_boundary(path: Path) -> Polygon | MultiPolygon:
    data = json.loads(path.read_text())
    if data.get("type") == "FeatureCollection":
        geoms = [shape(f["geometry"]) for f in data["features"]]
        return unary_union(geoms)
    if data.get("type") == "Feature":
        return shape(data["geometry"])
    return shape(data)


def polygon_to_overpass_poly(geom: Polygon | MultiPolygon) -> str:
    """Overpass `poly:` filter wants "lat lon lat lon ..." pairs, single ring.
    For MultiPolygon we use the convex hull as a conservative superset — OSM
    results outside the true boundary are still clipped in Python afterward."""
    if isinstance(geom, MultiPolygon):
        geom = geom.convex_hull
    coords = list(geom.exterior.coords)
    return " ".join(f"{lat} {lon}" for lon, lat, *_ in coords)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
def fetch_osm_water_points(geom: Polygon | MultiPolygon) -> list[WaterPointRecord]:
    poly = polygon_to_overpass_poly(geom)
    query = f"""
    [out:json][timeout:120];
    (
      node["man_made"="water_well"](poly:"{poly}");
      node["man_made"="water_tap"](poly:"{poly}");
      node["amenity"="drinking_water"](poly:"{poly}");
      node["waterway"="water_point"](poly:"{poly}");
      node["natural"="spring"](poly:"{poly}");
      way["natural"="water"](poly:"{poly}");
      way["waterway"="dam"](poly:"{poly}");
    );
    out center;
    """
    log.info("Querying Overpass API for OSM water features...")
    # Overpass rejects requests without a proper User-Agent (406 Not Acceptable).
    resp = httpx.post(
        OVERPASS_URL,
        data={"data": query},
        timeout=180,
        headers={"User-Agent": "arda-piosphere/1.0 (water-point import; contact: arda@example.com)"},
    )
    resp.raise_for_status()
    elements = resp.json().get("elements", [])

    records: list[WaterPointRecord] = []
    for el in elements:
        if el["type"] == "node":
            lon, lat = el["lon"], el["lat"]
        elif "center" in el:
            lon, lat = el["center"]["lon"], el["center"]["lat"]
        else:
            continue
        if not geom.contains(Point(lon, lat)):
            continue
        records.append(
            WaterPointRecord(
                lon=lon,
                lat=lat,
                source_type="osm",
                source_ref=f"{el['type']}/{el['id']}",
                confidence=0.55,  # OSM: community-mapped, unverified by default
                name=(el.get("tags") or {}).get("name"),
            )
        )
    log.info(f"OSM: {len(records)} water points inside boundary")
    return records


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
def fetch_wpdx_water_points(geom: Polygon | MultiPolygon) -> list[WaterPointRecord]:
    """WPDx Socrata API, filtered to Kenya + Isiolo county via SoQL, then
    clipped to the exact boundary polygon in Python (API only supports
    country/adm1/adm2 text filters, not arbitrary polygons)."""
    settings = get_settings()
    minx, miny, maxx, maxy = geom.bounds
    params = {
        "$where": (
            f"clean_country_name='Kenya' AND clean_adm1='Isiolo' "
            f"AND lat_deg between {miny} and {maxy} "
            f"AND lon_deg between {minx} and {maxx}"
        ),
        "$limit": 50000,
    }
    headers = {}
    if settings.wpdx_api_key:
        headers["X-App-Token"] = settings.wpdx_api_key

    log.info("Querying WPDx API for water points...")
    resp = httpx.get(WPDX_API_URL, params=params, headers=headers, timeout=60)
    resp.raise_for_status()
    rows = resp.json()

    records: list[WaterPointRecord] = []
    for row in rows:
        try:
            lon, lat = float(row["lon_deg"]), float(row["lat_deg"])
        except (KeyError, ValueError, TypeError):
            continue
        if not geom.contains(Point(lon, lat)):
            continue
        status = str(row.get("status_id", "")).lower()
        confidence = 0.75 if "yes" in status else 0.5
        records.append(
            WaterPointRecord(
                lon=lon,
                lat=lat,
                source_type="wpdx",
                source_ref=row.get("wpd_id") or row.get("row_id"),
                confidence=confidence,
                name=row.get("water_point_name") or row.get("name"),
            )
        )
    log.info(f"WPDx: {len(records)} water points inside boundary")
    return records


def dedupe(records: Iterable[WaterPointRecord], tolerance_deg: float = 0.0005) -> list[WaterPointRecord]:
    """Cheap grid-snap dedupe across sources (~50m at this latitude). WPDx is
    treated as authoritative over OSM when both land in the same cell."""
    seen: dict[tuple[float, float], WaterPointRecord] = {}
    priority = {"wpdx": 2, "ilri": 2, "osm": 1, "satellite_gsw": 0, "ground_truth": 3}
    for rec in records:
        key = (round(rec.lon / tolerance_deg), round(rec.lat / tolerance_deg))
        existing = seen.get(key)
        if existing is None or priority[rec.source_type] > priority[existing.source_type]:
            seen[key] = rec
    return list(seen.values())


def upsert_water_sources(records: list[WaterPointRecord], ward: str | None, county: str) -> int:
    client = get_supabase_client()
    rows = [
        {
            "geom": f"SRID=4326;POINT({r.lon} {r.lat})",
            "source_type": r.source_type,
            "source_ref": r.source_ref,
            "name": r.name,
            "ward": ward,
            "county": county,
            "confidence": r.confidence,
        }
        for r in records
    ]
    inserted = 0
    batch_size = 500
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        client.table("water_sources").insert(batch).execute()
        inserted += len(batch)
        log.info(f"Inserted {inserted}/{len(rows)} water_sources rows...")
    return inserted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boundary", required=True, type=Path, help="GeoJSON polygon of ward or county")
    parser.add_argument("--ward", default=None, help="Ward name (omit for full-county run)")
    parser.add_argument("--county", default="Isiolo")
    parser.add_argument("--source", choices=["osm", "wpdx", "both"], default="both")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + dedupe but do not write to DB")
    args = parser.parse_args()

    geom = load_boundary(args.boundary)
    log.info(f"Loaded boundary for ward={args.ward!r} county={args.county!r}, bounds={geom.bounds}")

    records: list[WaterPointRecord] = []
    if args.source in ("osm", "both"):
        records += fetch_osm_water_points(geom)
    if args.source in ("wpdx", "both"):
        records += fetch_wpdx_water_points(geom)

    deduped = dedupe(records)
    log.info(f"Total after dedupe: {len(deduped)} (from {len(records)} raw)")

    if args.dry_run:
        log.info("Dry run — not writing to DB. Sample records:")
        for r in deduped[:5]:
            log.info(r)
        return

    count = upsert_water_sources(deduped, args.ward, args.county)
    log.info(f"Done. Inserted {count} water_sources rows.")


if __name__ == "__main__":
    main()
