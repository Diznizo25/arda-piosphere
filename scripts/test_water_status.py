"""Water-status logic: one-tap herder reports, staleness, and guidance gating.

No DB / no network — pure functions, fast to run anywhere.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from app.services import water_status as ws  # noqa: E402

NOW = datetime(2026, 3, 1, tzinfo=timezone.utc)


def days_ago(n: float) -> datetime:
    return NOW - timedelta(days=n)


# --- 1) one-tap replies -------------------------------------------------------
assert ws.intent_for_digit("1") == "water_available"
assert ws.intent_for_digit(" 2 ") == "water_dry"
assert ws.intent_for_digit("3") == "water_not_found"
assert ws.intent_for_digit("4") == "water_broken"
assert ws.intent_for_digit("5") == "water_intermittent"
assert ws.intent_for_digit("12") is None, "a herd size must not be read as a water report"
assert ws.intent_for_digit("maji yapo") is None
print("digit quick-replies OK (1..5 -> report intents, other numbers ignored)")

# --- 2) report -> status -----------------------------------------------------
assert ws.status_for_report("water_dry") == ws.STATUS_DRY
assert ws.status_for_report("water_available") == ws.STATUS_FUNCTIONAL
assert ws.status_for_report("water_broken") == ws.STATUS_BROKEN
assert ws.status_for_report("water_not_found") == ws.STATUS_NOT_FOUND
assert ws.status_for_report("pasture_good") is None
print("report -> status mapping OK")

# --- 3) gating: a recently-dry point must not be used -------------------------
assert not ws.usable_for_guidance(ws.STATUS_DRY, days_ago(2), now=NOW)
assert not ws.usable_for_guidance(ws.STATUS_BROKEN, days_ago(30), now=NOW)
assert not ws.usable_for_guidance(ws.STATUS_NOT_FOUND, days_ago(59), now=NOW)
# ...but after the staleness window it comes back (a pan can refill)
assert ws.usable_for_guidance(ws.STATUS_DRY, days_ago(61), now=NOW)
# confirmed-good points are always usable; unknown ones are usable but flagged
assert ws.usable_for_guidance(ws.STATUS_FUNCTIONAL, days_ago(1), now=NOW)
assert ws.usable_for_guidance(ws.STATUS_UNKNOWN, None, now=NOW)
print("gating OK (dry/broken/not-found suppressed for 60 days, then re-checked)")

# --- 4) warning flag: stale or unconfirmed -------------------------------
assert ws.needs_check(ws.STATUS_DRY, days_ago(5), now=NOW)
assert not ws.needs_check(ws.STATUS_FUNCTIONAL, days_ago(5), now=NOW)
assert ws.needs_check(ws.STATUS_FUNCTIONAL, days_ago(90), now=NOW)
assert ws.needs_check(ws.STATUS_UNKNOWN, None, now=NOW), "never confirmed => warn"
assert ws.needs_check(ws.STATUS_UNKNOWN, None, days_ago(3), now=NOW) is False, \
    "a recent herd confirmation counts even without a status"
print("staleness warning OK (unknown or >45 days => 'confirm before you go')")

# --- 5) wording + colours ----------------------------------------------------
assert "imekauka" in ws.status_label(ws.STATUS_DRY, "swa")
assert "dry" in ws.status_label(ws.STATUS_DRY, "eng")
assert ws.status_colour(ws.STATUS_DRY) == "#b91c1c"
assert ws.status_colour(ws.STATUS_FUNCTIONAL) == "#0ea5e9"
assert ws.status_colour(ws.STATUS_UNKNOWN) == "#6b7280"
q = ws.check_question("swa")
assert "1 maji yapo" in q and "imekauka" in q
assert "Asante" in ws.thanks_for_report("water_dry", "swa")
assert "Recorded" in ws.thanks_for_report("water_dry", "eng")
print("labels/question/thanks OK")

# --- 6) the guidance queries must actually carry the gate --------------------
from app.services import water_reach as wr  # noqa: E402

for name in ("NEAREST_REACHABLE_SQL", "NEAREST_REACHABLE_DIST_SQL"):
    sql = getattr(wr, name)
    assert "ws.status in ('dry','broken','not_found')" in sql, f"{name} missing status gate"
    assert "%(stale_days)s" in sql, f"{name} missing stale-days parameter"
    assert "ws.status" in sql and "status_updated_at" in sql
print("guidance SQL carries the dry/broken/not-found gate OK")

assert "status" in wr.NEARBY_SQL and "status_updated_at" in wr.NEARBY_SQL, \
    "nearby-water list must expose status for the menu and the map"
print("water-status tests OK")

