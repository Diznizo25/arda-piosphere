"""End-to-end advisory smoke test against real R2 COGs + DB rings.

Runs the production read path (raster_read.read_zone_stats, which streams the
8x overview from R2) for every water source that has a COG, and classifies
forage condition + water reliability for each species ring.

Usage:
  python scripts/smoke_advisory.py [--water-source ID]
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, ".")
from app.db import get_supabase_client  # noqa: E402
from app.services.advisory_logic import (  # noqa: E402
    classify_forage_condition,
    classify_water_reliability,
)
from app.services.raster_read import read_zone_stats  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--water-source", help="Limit to one water source id")
    args = parser.parse_args()

    client = get_supabase_client()
    zones = (
        client.table("piosphere_zones")
        .select("water_source_id,species,radius_km,geom")
        .execute().data
    )
    zones_by_ws: dict[str, list[dict]] = {}
    for z in zones:
        zones_by_ws.setdefault(z["water_source_id"], []).append(z)

    water_sources = client.table("water_sources").select("id,ward,county,source_type").execute().data
    if args.water_source:
        water_sources = [w for w in water_sources if w["id"] == args.water_source]

    print(f"Loaded {len(water_sources)} water sources, {len(zones)} zones\n")
    ok_count = 0
    for ws in sorted(water_sources, key=lambda w: w["id"]):
        ws_id = ws["id"]
        print(f"=== {ws_id[:8]} ({ws.get('ward')}/{ws.get('county')}) ===")
        for z in sorted(zones_by_ws.get(ws_id, []), key=lambda x: x["species"]):
            species = z["species"]
            try:
                stats = read_zone_stats(ws_id, json.dumps(z["geom"]))
                forage = classify_forage_condition(stats.means)
                water = classify_water_reliability(stats.means.get("GSW_MONTHLY_RECURRENCE"))
            except Exception as e:  # noqa: BLE001
                print(f"  [{species:<6}] ERROR: {e}")
                continue
            m = stats.means
            print(
                f"  [{species:<6} r={z['radius_km']:>2}km] px={stats.valid_pixel_count:>6} "
                f"NDVI={m.get('NDVI', float('nan')):+.3f} SATVI={m.get('SATVI', float('nan')):+.3f} "
                f"BSI={m.get('BSI', float('nan')):+.3f} VCI={m.get('VCI', float('nan')):5.1f} "
                f"GSW={m.get('GSW_MONTHLY_RECURRENCE', float('nan')):5.1f} "
                f"-> {forage.condition.value} (norm={forage.seasonally_normal}, "
                f"{forage.curing_stage_note or 'n/a'}) | water={water.value}"
            )
            ok_count += 1

    print(f"\nRead path OK for {ok_count} (species, zone) pairs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
