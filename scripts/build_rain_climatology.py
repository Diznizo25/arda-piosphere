"""
Build the rainfall climatology per water point from CHIRPS (GEE) — the "what is
normal here, in this month" baseline that makes a dry spell mean something.

Why CHIRPS and not the forecast API: climatology needs 30 years of consistent,
gridded, Africa-validated rainfall. CHIRPS daily (UCSB-CHG/CHIRPS/DAILY, ~5.5 km,
1981-present) is already reachable with the service account we use for everything
else, costs nothing, and cannot be silently revised under us the way a model
reanalysis can.

What it writes: rainfall_climatology (per water point, per calendar month, mean +
p10/p90). Copying this 30-year baseline out of GEE into our DB means the live
advisory can compare "20 mm this month" against normal WITHOUT any Earth Engine
call at request time (architecture principle #1).

Usage:
  python scripts/build_rain_climatology.py --all
  python scripts/build_rain_climatology.py --water-source <id>
  python scripts/build_rain_climatology.py --all --since 1991 --until 2020
"""
from __future__ import annotations

import argparse
import logging
import sys

sys.path.insert(0, ".")

from app.db import get_pg_connection  # noqa: E402
from app.services import environment  # noqa: E402
from app.services.gee_auth import init_earth_engine  # noqa: E402

log = logging.getLogger("build_rain_climatology")

CHIRPS = "UCSB-CHG/CHIRPS/DAILY"
CHIRPS_SCALE_M = 5566  # CHIRPS native grid (~0.05 degrees)


def monthly_normals(lon: float, lat: float, since: int, until: int,
                    buffer_km: float) -> tuple[dict[int, dict], int]:
    """Mean and p10/p90 monthly rainfall for one point, from CHIRPS.

    One GEE request per calendar month (not per year-month): each request reduces
    a 30-image stack to a mean + percentiles image and samples it at the point.
    That keeps the whole build to ~12 server round trips per water point.
    """
    import ee

    region = ee.Geometry.Point([lon, lat]).buffer(buffer_km * 1000)
    coll = (ee.ImageCollection(CHIRPS).select("precipitation")
            .filterBounds(region)
            .filterDate(f"{since}-01-01", f"{until}-12-31"))
    point = ee.FeatureCollection([ee.Feature(region, {"pid": 1})])
    years = list(range(since, until + 1))

    out: dict[int, dict] = {}
    for month in range(1, 13):
        images = []
        for year in years:
            start = ee.Date.fromYMD(year, month, 1)
            images.append(coll.filterDate(start, start.advance(1, "month")).sum())
        stack = ee.ImageCollection(images)
        stats = stack.reduce(
            ee.Reducer.mean().combine(ee.Reducer.percentile([10, 90]), sharedInputs=True)
        ).rename(["mean", "p10", "p90"])
        sampled = stats.reduceRegions(
            collection=point, reducer=ee.Reducer.first(),
            scale=CHIRPS_SCALE_M, tileScale=4,
        ).first().getInfo()
        props = (sampled or {}).get("properties", {}) or {}
        if props.get("mean") is None:
            log.warning("month %02d: CHIRPS returned no pixels for (%.4f, %.4f)", month, lon, lat)
            continue
        out[month] = {"mean": props["mean"], "p10": props.get("p10"), "p90": props.get("p90")}
    return out, len(years)


def _scope(water_source_id: str | None) -> list[dict]:
    points = environment.list_points()
    if water_source_id:
        points = [p for p in points if p["id"] == water_source_id]
    return points


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all", action="store_true", help="every water point")
    scope.add_argument("--water-source", help="a single water_source id")
    parser.add_argument("--since", type=int, default=1991)
    parser.add_argument("--until", type=int, default=2020)
    parser.add_argument("--buffer-km", type=float, default=6.0,
                        help="sample radius around the point (CHIRPS pixels are ~5.5 km)")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute and print, do not write to the DB")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_earth_engine()

    points = _scope(args.water_source)
    if not points:
        log.error("no water points matched the scope")
        return 1

    written = 0
    for p in points:
        label = p["name"] or p["ward"] or p["id"]
        normals, years = monthly_normals(p["lon"], p["lat"], args.since, args.until,
                                        args.buffer_km)
        if not normals:
            log.error("%s: no climatology computed", label)
            continue
        annual = sum(v["mean"] for v in normals.values())
        log.info("%s: %d months, normal annual rainfall %.0f mm", label, len(normals), annual)
        if args.dry_run:
            for month in sorted(normals):
                v = normals[month]
                log.info("  %02d mean %.1f mm p10 %.1f p90 %.1f", month, v["mean"],
                         v["p10"] or 0.0, v["p90"] or 0.0)
            continue
        written += environment.store_climatology(p["id"], normals, years=years, source="chirps")

    log.info("done — %d monthly normals written", written)
    return 0


if __name__ == "__main__":
    sys.exit(main())

