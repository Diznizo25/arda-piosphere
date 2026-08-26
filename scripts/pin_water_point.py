"""
Register a brand-new water point from coordinates and (optionally) run the full
compute+transfer pipeline for just that point so it gets grazing data quickly.

One command, from the export machine (needs DATABASE_URL + GEE + R2 env vars):

    python scripts/pin_water_point.py --lat 0.5854 --lon 36.9915 --name "Oldonyiro Borehole"

With --compute it also runs, for just this point:
    1. generate_piosphere_zones.py --water-source <id>   (idempotent zone refresh)
    2. gee_compute_export.py --water-source <id>         (GEE export to Drive/GCS)
    3. transfer_assets_to_r2.py --asset <id> --force     (merge + upload COG + overview)
    4. build_overview_cogs.py                             (rebuild 8x overviews)

Without --compute it only registers the point + species rings (takes seconds)
and prints the exact commands to run the compute later.
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.services.water_sources import create_water_source  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pin_water_point")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--name", help="Source reference/name (stored in source_ref)")
    parser.add_argument("--source-type", default="ground_truth",
                        choices=["satellite_gsw", "osm", "wpdx", "ilri", "ground_truth"])
    parser.add_argument("--ward")
    parser.add_argument("--county", default="Isiolo")
    parser.add_argument("--confidence", type=float, default=0.5)
    parser.add_argument("--compute", action="store_true",
                        help="Also run zones + GEE compute + transfer for this point now.")
    args = parser.parse_args()

    ws = create_water_source(
        lon=args.lon,
        lat=args.lat,
        source_type=args.source_type,
        source_ref=args.name,
        ward=args.ward,
        county=args.county,
        confidence=args.confidence,
    )
    print(f"Registered water source {ws.id} at ({ws.lat:.5f}, {ws.lon:.5f}) with 3 species rings.")

    if not args.compute:
        print("\nGrazing data will appear after compute. Run:")
        print(f"  python scripts/gee_compute_export.py --water-source {ws.id}")
        print(f"  python scripts/transfer_assets_to_r2.py --asset {ws.id} --force")
        print("  python scripts/build_overview_cogs.py")
        return 0

    scripts = [
        ("generate_piosphere_zones.py", ["--water-source", ws.id]),
        ("gee_compute_export.py", ["--water-source", ws.id]),
        ("transfer_assets_to_r2.py", ["--asset", ws.id, "--force"]),
        ("build_overview_cogs.py", []),
    ]
    for script, extra in scripts:
        cmd = [sys.executable, str(Path(__file__).resolve().parent / script), *extra]
        log.info(f"Running: {' '.join(cmd)}")
        proc = subprocess.run(cmd)
        if proc.returncode != 0:
            log.error(f"{script} failed with exit code {proc.returncode} — stopping.")
            return proc.returncode

    log.info(f"Done. Water source {ws.id} is live with grazing data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
