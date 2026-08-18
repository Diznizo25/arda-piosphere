"""
Step 5 validation: read-path + species-ring advisory logic on Oldonyiro.

Reads the real COGs (local merged.tif, byte-identical to the objects uploaded
to R2), clips them to the real piosphere zone polygons from the database,
computes per-band means, and runs the production advisory classification
(advisory_logic). Confirms the Step 5 acceptance criterion: cattle vs shoat vs
camel at the same water point yield different, sensible results where the
species rings differ.

Uses tifffile + shapely (both import cleanly on this machine) instead of
rasterio, which is blocked here by the Windows Application Control policy.

Usage:
  python scripts/validate_step5.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import shapely.geometry
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.db import get_supabase_client  # noqa: E402
from app.services.advisory_logic import (  # noqa: E402
    classify_forage_condition,
    classify_water_reliability,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("validate_step5")

BAND_NAMES = ["NDVI", "NDRE", "SATVI", "BSI", "NDMI", "NDWI", "VCI", "GSW_MONTHLY_RECURRENCE"]
TILE_DIR = Path("data/tiles")


def read_zone_means(cog_path: Path, geojson: dict) -> dict[str, float]:
    """Read the COG and return per-band means over pixels inside `geojson`.

    The merged COG was written by the tifffile merge with ModelPixelScale +
    ModelTiepoint (or, for raw GEE tiles, ModelTransformationTag). Pixel (row,
    col) center maps to x = x0 + (col+0.5)*px, y = y0 - (row+0.5)*py for a
    north-up raster (y0 = top edge).
    """
    geom = shapely.geometry.shape(geojson)

    with tifffile.TiffFile(cog_path) as tif:
        arr = tif.asarray()  # interleaved -> (rows, cols, bands)
        page = tif.pages[0]
        tags = page.tags
        if 34264 in tags:  # raw GEE tile transform matrix
            m = tags[34264].value
            x0, px = m[3], abs(m[0])
            y0, py = m[7], abs(m[5])
        else:
            scale = tags[33550].value
            tie = tags[33922].value
            px, py = abs(scale[0]), abs(scale[1])
            x0, y0 = tie[3], tie[4]

    rows, cols, bands = arr.shape
    from shapely import contains_xy

    # Crop the mask computation to the polygon's bounding box (rings are a small
    # fraction of the COG extent) — big speedup on 5000x5000 grids.
    minx, miny, maxx, maxy = geom.bounds
    c0 = max(0, int((minx - x0) / px))
    c1 = min(cols, int((maxx - x0) / px) + 1)
    r0 = max(0, int((y0 - maxy) / py))
    r1 = min(rows, int((y0 - miny) / py) + 1)
    if c1 <= c0 or r1 <= r0:
        return {"_n_pixels": 0.0}

    xs = x0 + (np.arange(c0, c1) + 0.5) * px
    ys = y0 - (np.arange(r0, r1) + 0.5) * py
    # Sample every 4th pixel for the mask/means — plenty for zone means and a
    # ~16x speedup on 5000x5000 grids (full-resolution masking is what the
    # server does with rasterio; this is a validation approximation).
    STEP = 4
    xs_s = xs[::STEP]
    ys_s = ys[::STEP]
    X, Y = np.meshgrid(xs_s, ys_s)
    mask = contains_xy(geom, X, Y)
    n_px = int(mask.sum())

    means: dict[str, float] = {}
    for i in range(bands):
        name = BAND_NAMES[i] if i < len(BAND_NAMES) else f"band_{i + 1}"
        data = arr[r0:r1, c0:c1, i][::STEP, ::STEP][mask]
        data = data[np.isfinite(data)]
        means[name] = float(np.mean(data)) if data.size else float("nan")
    means["_n_pixels"] = float(n_px)
    return means


def main() -> int:
    client = get_supabase_client()

    # Water sources + all their zones from the DB.
    ws_rows = client.table("water_sources").select("id,ward,county,source_type").execute().data
    zone_rows = (
        client.table("piosphere_zones")
        .select("water_source_id,species,radius_km,geom")
        .execute().data
    )
    zones_by_ws: dict[str, list[dict]] = {}
    for z in zone_rows:
        zones_by_ws.setdefault(z["water_source_id"], []).append(z)
    log.info(f"Loaded {len(ws_rows)} water sources, {len(zone_rows)} zones")

    all_ok = True
    for ws in sorted(ws_rows, key=lambda w: w["id"]):
        ws_id = ws["id"]
        cog = TILE_DIR / ws_id / "merged.tif"
        if not cog.exists():
            log.warning(f"  {ws_id[:8]} missing local COG — skipping")
            all_ok = False
            continue

        print(f"\n=== {ws_id[:8]} ({ws.get('ward')} / {ws.get('source_type')}) ===")
        for z in sorted(zones_by_ws.get(ws_id, []), key=lambda x: x["species"]):
            species = z["species"]
            means = read_zone_means(cog, z["geom"])
            forage = classify_forage_condition(means)
            water = classify_water_reliability(means.get("GSW_MONTHLY_RECURRENCE", 0.0))
            row = (
                f"  [{species:<6} r={z['radius_km']:<5}] "
                f"px={means['_n_pixels']:<8.0f} "
                f"NDVI={means.get('NDVI', float('nan')):+.3f} "
                f"SATVI={means.get('SATVI', float('nan')):+.3f} "
                f"BSI={means.get('BSI', float('nan')):+.3f} "
                f"VCI={means.get('VCI', float('nan')):5.1f} "
                f"GSW={means.get('GSW_MONTHLY_RECURRENCE', float('nan')):5.1f} "
                f"-> {forage.condition.value} (seasonal_norm={forage.seasonally_normal}, "
                f"{forage.curing_stage_note or 'n/a'}) | water={water.value}"
            )
            print(row)

    print("\n=== Step 5 acceptance check ===")
    print("Verify visually above: species rows differ (cattle != camel) where rings differ,")
    print("and each classification is sensible for a semi-arid rangeland.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
