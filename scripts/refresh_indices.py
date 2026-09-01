"""
Two-week satellite refresh orchestrator.

Recomputes the satellite index stack (NDVI/NDRE/SATVI/BSI/NDMI/NDWI/VCI/GSW)
for a scope and pushes the updated COGs + 8x overviews to R2:

    1. generate_piosphere_zones.py --<scope>   (idempotent; keeps rings current)
    2. gee_export_to_asset.py --<scope> --as-of-date <today> --export-only --force
                                             (GEE recompute -> FREE GEE-Asset export)
    3. transfer_assets_to_r2.py --force --fresh   (assets -> R2 COGs + overviews)
    4. build_overview_cogs.py --force         (rebuild 8x reads from cached COGs)

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


def _run(script: str, args: list[str], retries: int = 1, wait_s: int = 60,
         fatal: bool = False) -> int:
    """Run a pipeline sub-script with retries.

    Returns the last exit code. When `fatal` is False (the default) a persistent
    failure is logged and the refresh CONTINUES — the workflow always exits 0 so
    GitHub never reports a failed run; per-point problems are self-healing (the
    next scheduled run re-exports assets that are missing).
    """
    import time

    code = -1
    for attempt in range(retries + 1):
        cmd = [sys.executable, str(SCRIPTS / script), *args]
        log.info("Running: %s", " ".join(cmd))
        proc = subprocess.run(cmd)
        code = proc.returncode
        if code == 0:
            return 0
        if attempt < retries:
            log.warning(f"{script} failed (exit {code}); retry {attempt + 1}/{retries} in {wait_s}s")
            time.sleep(wait_s)
            continue
        if fatal:
            log.error(f"{script} failed with exit code {code} — aborting refresh.")
            return code
        log.error(f"{script} failed with exit code {code} — continuing (non-fatal).")
    return code


def _report_to_dashboard(scope_label: str, steps: list[tuple[str, int]]) -> None:
    """Best-effort: surface the refresh outcome in the ops dashboard activity
    feed via query_log (kind='other'). Fail-open — never raises."""
    try:
        from app.services import query_log

        errors = [name for name, code in steps if code != 0]
        query_log.log_query(
            kind="other",
            result="ok" if not errors else "error",
            detail={
                "pipeline": "refresh-indices",
                "scope": scope_label,
                "steps": {name: "ok" if code == 0 else f"exit {code}" for name, code in steps},
                "failed_steps": errors,
            },
        )
    except Exception:  # noqa: BLE001
        log.exception("dashboard report failed (non-fatal)")


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

    steps: list[tuple[str, int]] = []

    # 1. Keep the species rings current (idempotent).
    steps.append(("generate_zones", _run("generate_piosphere_zones.py", scope_args)))

    # 2. Recompute the index stack and export it as a GEE ASSET. The GEE-asset
    #    path is FREE — the service account has 0 Drive/GCS storage quota, so
    #    the old gee_compute_export.py Drive path always fails here. --force
    #    deletes the previous asset so the two-week refresh really gets fresh
    #    satellite data (without it, asset_exists() skips every point).
    steps.append(("gee_export",
                  _run("gee_export_to_asset.py",
                       [*scope_args, "--as-of-date", today, "--export-only", "--force"])))

    # 3. Transfer the (re)exported assets to R2 as COGs + 8x overviews. The
    #    transfer script takes --force/--fresh and is scoped to the same ward/
    #    county (or --asset for a single point) so an unchanged point is never
    #    re-downloaded. It never exits non-zero — per-asset failures are logged
    #    and retried by the next run.
    if args.water_source:
        transfer_args = ["--asset", args.water_source]
    else:
        transfer_args = scope_args
    steps.append(("transfer",
                  _run("transfer_assets_to_r2.py", [*transfer_args, "--force", "--fresh"])))

    # 4. Rebuild 8x overviews from the cached merged COGs (cheap; skips any
    #    already uploaded by step 3).
    steps.append(("overviews", _run("build_overview_cogs.py", ["--force"])))

    failed = [(name, code) for name, code in steps if code != 0]
    if failed:
        log.warning("REFRESH COMPLETE with %d step problem(s): %s — the next "
                    "scheduled run will retry automatically.",
                    len(failed), ", ".join(f"{n} (exit {c})" for n, c in failed))
    else:
        log.info("REFRESH COMPLETE — all steps passed.")

    _report_to_dashboard(scope_label, steps)
    # NEVER exit non-zero: a GitHub failure email must not be possible. Per-step
    # problems are logged, reported to the dashboard feed, and retried next run.
    return 0


if __name__ == "__main__":
    sys.exit(main())
