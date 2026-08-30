"""Local advisory validation using local overview COGs (bypasses R2 read path).

Uses the same zone-mean + classification logic as the production read path but
reads from the local data/tiles/*/overview_8x.tif files. Validates that the
real COG data produces sensible per-species advisories.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from shapely.geometry import shape

sys.path.insert(0, ".")
from app.config import get_settings  # noqa: E402
from app.db import get_pg_connection  # noqa: E402
from app.services.advisory_logic import (  # noqa: E402
    classify_forage_condition,
    classify_water_reliability,
)
from app.services.raster_read import BAND_NAMES, _read_band_means  # noqa: E402


def main() -> int:
    get_settings()
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select pz.water_source_id, pz.species, pz.radius_km,
                       st_asgeojson(pz.geom) as geom_geojson
                from piosphere_zones pz
                join water_sources ws on ws.id = pz.water_source_id
                order by pz.water_source_id, pz.species
                """
            )
            rows = cur.fetchall()

    print(f"Loaded {len(rows)} (water_source, species) zones\n")
    for r in rows:
        ws_id = str(r["water_source_id"])
        ov = Path("data/tiles") / ws_id / "overview_8x.tif"
        if not ov.exists():
            continue
        geom = shape(json.loads(r["geom_geojson"]))
        with rasterio.open(str(ov)) as src:
            out = src.read(masked=True)
            transform = src.transform
        stats = _read_band_means(out, transform, geom)
        forage = classify_forage_condition(stats.means)
        water = classify_water_reliability(stats.means.get("GSW_MONTHLY_RECURRENCE"))
        m = stats.means
        print(
            f"{ws_id[:8]} [{r['species']:<6} r={r['radius_km']:>2}km] "
            f"px={stats.valid_pixel_count:>6} NDVI={m.get('NDVI', float('nan')):+.3f} "
            f"SATVI={m.get('SATVI', float('nan')):+.3f} BSI={m.get('BSI', float('nan')):+.3f} "
            f"VCI={m.get('VCI', float('nan')):5.1f} GSW={m.get('GSW_MONTHLY_RECURRENCE', float('nan')):5.1f} "
            f"-> {forage.condition.value} (norm={forage.seasonally_normal}, "
            f"{forage.curing_stage_note or 'n/a'}) | water={water.value}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
