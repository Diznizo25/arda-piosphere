"""Data-freshness tests: the status logic and the panel contract.

The panel exists because "is our data current?" was invisible. These checks pin the
classification (so a source cannot silently drift from "fresh" to "stale" unnoticed)
and the shape the dashboard template reads.

Run: python scripts/test_freshness.py
"""
from __future__ import annotations

import io
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from app.services import dashboard_service as ds  # noqa: E402

# --- 1) the classification every row depends on ------------------------------
HOUR, DAY = 3600, 86400
assert ds.freshness_status(None, 14 * DAY) == "missing"
assert ds.freshness_status(0, 14 * DAY) == "fresh"
assert ds.freshness_status(14 * DAY, 14 * DAY) == "fresh"          # exactly on time
assert ds.freshness_status(14 * DAY + 1, 14 * DAY) == "aging"      # just late
assert ds.freshness_status(28 * DAY, 14 * DAY) == "aging"          # a full cycle late
assert ds.freshness_status(28 * DAY + 1, 14 * DAY) == "stale"
assert ds.freshness_status(13 * HOUR, 12 * HOUR) == "aging"        # weather, late
print("freshness classification OK")

# --- 2) age maths, including the awkward inputs ------------------------------
now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
assert ds.age_seconds(now - timedelta(hours=4), now) == 4 * HOUR
assert ds.age_seconds("2026-09-24T06:00:00+00:00", now) == 6 * HOUR
assert ds.age_seconds("2026-09-24T06:00:00Z", now) == 6 * HOUR      # Z suffix
assert ds.age_seconds(datetime(2026, 9, 24, 6, 0), now) == 6 * HOUR  # naive -> UTC
assert ds.age_seconds(None, now) is None
assert ds.age_seconds("not a date", now) is None                     # never raises
print("age maths OK")

# --- 3) the cadences match what the pipelines actually promise ---------------
expected = {k: secs for k, _l, _d, secs in ds.FRESHNESS_SOURCES}
assert expected["satellite"] == 14 * DAY, "must match refresh-indices.yml cron"
assert expected["rain_observed"] == DAY, (
    "observed_on is a DATE column: expecting less than a day would show a permanent "
    "false 'aging' (today's rows are stored at 00:00)"
)
assert expected["rain_forecast"] == 12 * HOUR, "must match refresh-environment cron"
assert expected["climatology"] == 365 * DAY
assert expected["herder_reports"] == 7 * DAY
assert len(ds.FRESHNESS_SOURCES) == 5
# A real day-old observation must read as FRESH, not aging: that was the bug.
assert ds.freshness_status(int(13.7 * HOUR), expected["rain_observed"]) == "fresh"
assert ds.freshness_status(int(30 * HOUR), expected["rain_observed"]) == "aging"
print("cadences match the scheduled jobs OK")

# --- 4) the panel + template contract ---------------------------------------
src = io.open("app/services/dashboard_service.py", encoding="utf-8").read()
assert "def data_freshness(" in src
assert "def _pipeline_runs(" in src
assert "cog_key(" in src, "satellite age must come from the COG object, not a guess"
assert "_cache_put(\"cog_ages\"" in src, "HEAD-ing nine COGs on every poll is wasteful"

tpl = io.open("app/templates/dashboard.html", encoding="utf-8").read()
assert 'id="freshness"' in tpl, "the panel must exist in the page"
assert "renderFreshness(" in tpl
assert "/dashboard/api/freshness" in tpl, "the page must call the new endpoint"
assert "renderFreshness(window._f" in tpl, "and actually render what it fetched"
for status in ("fresh", "aging", "stale", "missing"):
    assert f"{status}:" in tpl or f"{status} " in tpl, f"status '{status}' unstyled in the UI"

router = io.open("app/routers/dashboard.py", encoding="utf-8").read()
assert '@router.get("/api/freshness"' in router
print("panel + endpoint contract OK")

print("freshness tests OK")
