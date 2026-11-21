"""A forecast is only worth storing if it can be WRONG. This is the referee.

scripts/backtest_depletion.py turns each stored forecast into one verdict. The
logic is small but the labels are load-bearing - "early" and "late" are not
symmetric mistakes:

  early  the ring ran out BEFORE the window we gave. The herder planned a move
         around a later date than the one that arrived. This is the error that
         costs a herd condition.
  late   it ran out after the window. We over-warned: a wasted move and a
         credibility cost, but nobody went hungry.

So these tests pin every boundary (before / inside / after / never), plus the two
cases where the honest answer is "do not score yet" - the future has not arrived,
or our own sampler stopped and we would be blaming the model for a missing row.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import date, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest_depletion import SCAN_SQL, summarise, verdict  # noqa: E402

TODAY = date(2026, 10, 18)
ISSUED = date(2026, 9, 1)


def row(**kw) -> dict:
    """An issued forecast as it comes back from SCAN_SQL."""
    base = {
        "id": 1, "water_source_id": "ws-1", "species": "cattle", "interval": "daily",
        "issued_on": ISSUED, "status": "projected", "usable_pct": 34,
        "days_left": 20, "days_left_lo": 18, "days_left_hi": 25,
        "rate_source": "dry", "dry_slope_pp_week": -3.0, "cause": "grazing_pressure",
        "curing": False, "snapshot_as_of": "2026-09-01", "runout_on": None,
        "last_on": TODAY,
    }
    base.update(kw)
    return base


# --- 1) inside the window is a hit --------------------------------------
outcome, when = verdict(row(runout_on=ISSUED + timedelta(days=21)), TODAY, 30, 90)
assert (outcome, when) == ("hit", ISSUED + timedelta(days=21)), (outcome, when)
# The two edges of the window belong to the hit as well.
assert verdict(row(runout_on=ISSUED + timedelta(days=18)), TODAY, 30, 90)[0] == "hit"
assert verdict(row(runout_on=ISSUED + timedelta(days=25)), TODAY, 30, 90)[0] == "hit"
print("hit: a run-out inside the window we gave is a hit, edges included")

# --- 2) before the window is the dangerous one --------------------------
early = ISSUED + timedelta(days=5)
assert verdict(row(runout_on=early), TODAY, 30, 90)[0] == "early"
# A ring already spent when we spoke is not early - it is exactly right.
assert verdict(row(runout_on=ISSUED, status="already_low", usable_pct=8,
                   days_left=0, days_left_lo=0, days_left_hi=0), TODAY, 30, 90)[0] == "hit"
print("early: ran out before the window (incl. the already-spent edge case)")

# --- 3) after the window is over-warning --------------------------------
assert verdict(row(runout_on=ISSUED + timedelta(days=41)), TODAY, 30, 90)[0] == "late"
print("late: ran out after the window - over-warning, scored as such")

# --- 4) a forecast with no date ----------------------------------------
stable = row(status="stable", days_left=None, days_left_lo=None, days_left_hi=None)
assert verdict(stable, TODAY, 30, 90)[0] == "no_collapse", verdict(stable, TODAY, 30, 90)
# ... unless it ran out anyway: "stable" then a collapse in ten days is a miss.
assert verdict({**stable, "runout_on": ISSUED + timedelta(days=10)},
               TODAY, 30, 90)[0] == "early"
# beyond_horizon ("lasts more than 60 days") that runs out in 30 is a miss too.
far = row(status="beyond_horizon", days_left=None, days_left_lo=None, days_left_hi=None,
          runout_on=ISSUED + timedelta(days=30))
assert verdict(far, TODAY, 30, 90)[0] == "early"
print("no date: stable stays 'no_collapse' only while it does not collapse")

# --- 5) "do not score yet" is a legitimate answer -----------------------
# Issued yesterday: not even the window has opened, so no verdict may be invented.
fresh = row(issued_on=TODAY - timedelta(days=1), last_on=TODAY)
assert verdict(fresh, TODAY, 30, 90) == (None, None), verdict(fresh, TODAY, 30, 90)
# Issued long ago, but our sampler stopped a week later: a missing row is not a
# model failure, so it stays unscored rather than being blamed on the forecast.
stalled = row(last_on=ISSUED + timedelta(days=6))
assert verdict(stalled, TODAY, 30, 90) == (None, None), verdict(stalled, TODAY, 30, 90)
# Enough time passed and the ring is still above the line: now it can be scored.
old = row(issued_on=TODAY - timedelta(days=60), last_on=TODAY)
assert verdict(old, TODAY, 30, 90)[0] == "late", verdict(old, TODAY, 30, 90)
print("unscored: the future has not arrived, or the samples stopped - never a guess")

# --- 6) there was never a claim ----------------------------------------
thin = row(status="no_history", days_left=None, days_left_lo=None, days_left_hi=None)
assert verdict(thin, TODAY, 30, 90)[0] == "unknown"
assert verdict(thin, TODAY, 30, 90)[1] is None
print("no_history: 'unknown', not 'hit' - there was nothing to be right about")

# --- 7) the horizon and the run-out line are in the query itself -------
# Both decisions are made in SQL, so a later edit that drops either bound would
# quietly count a collapse next season as evidence about a 25-day forecast.
assert "%(score_days)s" in SCAN_SQL and "usable_pct <=" in SCAN_SQL, SCAN_SQL
assert "%(runout)s" in SCAN_SQL
print("query: the run-out line and the scoring horizon are both bounded in SQL")

# --- 8) the report answers the question we actually care about ---------
sample = [
    {**row(), "outcome": "hit"},
    {**row(), "outcome": "hit", "rate_source": "all"},
    {**row(), "outcome": "early", "rate_source": "all"},
    {**row(), "outcome": "early", "rate_source": "dry", "curing": True,
     "runout_on": ISSUED + timedelta(days=4)},
    {**row(), "outcome": "no_collapse", "rate_source": "dry", "status": "stable"},
]
report = summarise(sample, skipped=3)
assert "scored 5" in report and "hit 2" in report, report
assert "not yet scoreable: 3" in report, report
assert "rate_source=dry" in report and "rate_source=all" in report, report
assert "curing was flagged on 1" in report, report
# A source that never dated anything must not print "0/0 inside the window": that
# reads like a failure, when it is really an empty question.
undated = summarise([{**row(), "outcome": "no_collapse", "status": "stable",
                      "days_left": None, "days_left_lo": None,
                      "days_left_hi": None, "rate_source": "all"}], skipped=0)
assert "no date issued" in undated and "0/0" not in undated, undated
print("report: splits the hit rate by which rate the forecast leaned on")

print("\nBACKTEST DEPLETION OK")
