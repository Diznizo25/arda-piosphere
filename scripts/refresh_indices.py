"""
Two-week satellite refresh orchestrator.

Recomputes the satellite index stack (NDVI/NDRE/SATVI/BSI/NDMI/NDWI/VCI/GSW)
for a scope and pushes the updated COGs + 8x overviews to R2:

    1. generate_piosphere_zones.py --<scope>   (idempotent; keeps rings current)
    2. gee_compute_export.py    --<scope> --as-of-date <today>   (GEE recompute)
    3. transfer_assets_to_r2.py --<scope> --force --fresh         (stale-tile-proof)
    4. build_overview_cogs.py   --force                           (rebuild 8x reads)

Runs from the export machine (needs DATABASE_URL + GEE key + R2 creds in .env)
or as a scheduled GitHub Actions job (see .github/workflows/refresh-indices.yml —
every 14 days). The advisory read path reads the 8x overviews, so the step-4
upload alone is what makes new data visible to herders quickly.

Usage:
  python scripts/refresh_indices.py --ward Oldonyiro
  python scripts/refresh_indices.py --water-source <id>    # single point
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from datetime import date
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("refresh_indices")

SCRIPTS = Path(__file__).resolve().parent


def _run(script: str, args: list[str]) -> None:
    cmd = [sys.executable, str(SCRIPTS / script), *args]
    log.info("Running: %s", " ".join(cmd))
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        log.error(f"{script} failed with exit code {proc.returncode} — aborting refresh.")
        sys.exit(proc.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--ward", help="Refresh one ward")
    scope.add_argument("--county", help="Refresh a full county")
    scope.add_argument("--water-source", help="Refresh a single water_source id")
    args = parser.parse_args()

    if args.ward:
        scope_args = ["--ward", args.ward]
        scope_label = f"ward={args.ward!r}"
    elif args.county:
        scope_args = ["--county", args.county]
        scope_label = f"county={args.county!r}"
    else:
        scope_args = ["--water-source", args.water_source]
        scope_label = f"water_source={args.water_source!r}"

    today = date.today().isoformat()
    log.info(f"Refreshing satellite indices for {scope_label} as-of {today}")

    _run("generate_piosphere_zones.py", scope_args)
    _run("gee_compute_export.py", [*scope_args, "--as-of-date", today])
    _run("transfer_assets_to_r2.py", [*scope_args, "--force", "--fresh"])
    _run("build_overview_cogs.py", ["--force"])

    log.info("Refresh complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
