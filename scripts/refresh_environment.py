"""
Refresh the environmental series + 16-day rain forecast for every water point.

Run this on a schedule (see .github/workflows/refresh-environment.yml, twice a
day) from the export machine or CI — never from the request path. It writes
environment_daily (observed rain/soil moisture) and environment_forecast (cached
forecast) so advisories can state a dry-spell length and a rain outlook without
touching a weather API while a herder waits.

Fail-open by design: a point that fails to refresh is logged and skipped, the run
still exits 0, and the advisory simply omits the rain line for that point.

Usage:
  python scripts/refresh_environment.py --all
  python scripts/refresh_environment.py --water-source <id>
"""
from __future__ import annotations

import argparse
import logging
import sys

sys.path.insert(0, ".")

from app.services import environment  # noqa: E402

log = logging.getLogger("refresh_environment")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all", action="store_true", help="every water point")
    scope.add_argument("--water-source", help="a single water_source id")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    points = environment.list_points()
    if args.water_source:
        points = [p for p in points if p["id"] == args.water_source]
    if not points:
        log.error("no water points matched the scope")
        return 1

    ok = failed = 0
    for p in points:
        label = p["name"] or p["ward"] or p["id"]
        report = environment.refresh_for_water_source(p["id"], p["lat"], p["lon"])
        if report.get("ok"):
            ok += 1
            log.info("%-28s %d days, forecast %d d / %.1f mm", label,
                     report["days_written"], report["horizon_days"],
                     report["forecast_total_mm"])
        else:
            failed += 1
            log.warning("%-28s FAILED (%s)", label, report.get("reason"))

    _report_to_dashboard(ok, failed)
    log.info("done — %d ok, %d failed", ok, failed)
    return 0


def _report_to_dashboard(ok: int, failed: int) -> None:
    """Best-effort activity-feed entry (kind='other'), same as refresh_indices."""
    try:
        from app.services import query_log

        query_log.log_query(
            kind="other", result="ok" if not failed else "error",
            detail={"pipeline": "refresh-environment", "ok": ok, "failed": failed},
        )
    except Exception:  # noqa: BLE001
        log.exception("dashboard report failed (non-fatal)")


if __name__ == "__main__":
    sys.exit(main())
