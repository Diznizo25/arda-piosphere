"""Score the depletion forecasts we issued, against what the ring actually did.

WHY THIS EXISTS
---------------
migration 016 started keeping every forecast (depletion_forecasts) instead of only
the sentence we sent. That only pays off if the rows are scored: "18-25 days" is
folklore until a ring is watched to see when it really ran out. This job is the
other half of the loop.

WHAT IT DECIDES (one row, one verdict)
--------------------------------------
The ring "ran out" on the first sample where usable% fell to RUNOUT_PCT or below.
Comparing that day with the window we told the herder:

  hit          it ran out INSIDE the window we gave - the forecast did its job
  early        it ran out BEFORE the window: the dangerous error, because he
               planned around a later date than the one that arrived
  late         it ran out AFTER the window: we over-warned (a wasted move, and a
               credibility cost, but not a hungry herd)
  no_collapse  no run-out seen within the scoring horizon, and we never dated one
               (stable / beyond_horizon). For a DATED forecast that never came
               true we use 'late', not 'no_collapse', so silence is not a pass.
  unknown      nothing to judge with: no_history forecasts, or a ring whose later
               samples have not arrived yet (left unscored, retried next run)

Anything past the scoring horizon is a different event: a ring that recovers for
three months and collapses in the next dry season must not be counted as evidence
about a 25-day forecast.

Usage:
  python scripts/backtest_depletion.py                 # report only (dry run)
  python scripts/backtest_depletion.py --write         # store the verdicts
  python scripts/backtest_depletion.py --wait-days 30  # how long to wait for a run-out
  python scripts/backtest_depletion.py --score-days 90 # furthest run-out we accept
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.db import get_pg_connection  # noqa: E402
from app.services import depletion  # noqa: E402

log = logging.getLogger("backtest_depletion")

# Unscored forecasts, with the two dates that decide the verdict computed in SQL
# rather than in Python: the run-out day (first sample at/below RUNOUT_PCT after
# issue) and the last day we have any sample for. One query, no per-row reads.
SCAN_SQL = """
select f.id, f.water_source_id, f.species, f.interval, f.issued_on, f.status,
       f.usable_pct, f.days_left, f.days_left_lo, f.days_left_hi,
       f.rate_source, f.dry_slope_pp_week, f.cause, f.curing, f.snapshot_as_of,
       (select min(s.sampled_on)
          from ring_pasture_samples s
         where s.water_source_id = f.water_source_id
           and s.species = f.species
           and s.interval = f.interval
           and s.sampled_on >= f.issued_on
           and s.sampled_on <= f.issued_on + %(score_days)s
           and s.usable_pct <= %(runout)s) as runout_on,
       (select max(s.sampled_on)
          from ring_pasture_samples s
         where s.water_source_id = f.water_source_id
           and s.species = f.species
           and s.interval = f.interval
           and s.sampled_on >= f.issued_on) as last_on
from depletion_forecasts f
where f.outcome is null
order by f.issued_on, f.id
"""

SCORE_SQL = """
update depletion_forecasts
set outcome = %(outcome)s,
    runout_on = %(runout_on)s,
    scored_on = current_date
where id = %(id)s
"""


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value



def verdict(row: dict, today: date, wait_days: int, score_days: int) -> tuple[str | None, date | None]:
    """The one-word verdict for one issued forecast, and the day the ring ran out.

    Returns (None, runout) when the future has not arrived yet: unscored, not
    guessed. A verdict we cannot support is worse than an empty column.
    """
    issued = _as_date(row["issued_on"])
    runout = _as_date(row["runout_on"])
    last = _as_date(row["last_on"])
    status = (row["status"] or "").lower()
    if issued is None:
        return "unknown", None
    if status == "no_history":
        # There was never a claim to score; saying "hit" would flatter the model.
        return "unknown", runout

    lo = row["days_left_lo"]
    hi = row["days_left_hi"]
    if row["days_left"] is not None and hi is None:
        lo, hi = lo if lo is not None else row["days_left"], row["days_left"]
    dated = row["days_left"] is not None and hi is not None

    if runout is not None:
        if not dated:
            # stable / beyond_horizon implied "not soon" and it ran out anyway.
            return ("late" if status == "already_low" else "early"), runout
        actual = (runout - issued).days
        if actual <= 0:
            return "hit", runout           # already spent when we said so
        if actual < (lo or 0):
            return "early", runout
        if actual > hi:
            return "late", runout
        return "hit", runout

    # No run-out inside the scoring horizon. Only now does the wait matter: a ring
    # sampled twice since issue has not had time to prove us right or wrong.
    settled = issued + timedelta(days=max(wait_days, (hi or 0) + 7))
    if today < settled:
        return None, None
    if last is not None and (last - issued).days < min(wait_days, score_days):
        return None, None                  # history stopped: the job, not the model
    return ("late" if dated else "no_collapse"), None


def summarise(scored: list[dict], skipped: int) -> str:
    """Print the counts that actually answer a question: does the signal work?

    The overall hit rate is the least interesting number here. What matters is
    whether a forecast that LEANED on a predictor (the dry-period slope, the
    near/far contrast, curing) did better than one that just fitted the long-run
    line - that is the only way to learn which signal earns its place.
    """
    counts = Counter(r["outcome"] for r in scored)
    lines = [f"scored {len(scored)}: " + ", ".join(
        f"{k} {v}" for k, v in sorted(counts.items()))]
    if skipped:
        lines.append(f"not yet scoreable: {skipped} (retried on the next run)")

    by_rate: dict[str, Counter] = defaultdict(Counter)
    for r in scored:
        by_rate[r.get("rate_source") or "unknown"][r["outcome"]] += 1
    for source, c in sorted(by_rate.items()):
        dated = sum(v for k, v in c.items() if k in ("hit", "early", "late"))
        hits = c.get("hit", 0)
        # A source that never dated anything gets no ratio: "0/0 inside the window"
        # reads like a failure and is really an empty question.
        head = (f"  rate_source={source}: {hits}/{dated} inside the window "
                f"({100 * hits // dated}% hit)" if dated
                else f"  rate_source={source}: no date issued")
        lines.append(head + "".join(
            f", {k} {c[k]}" for k in ("early", "late", "no_collapse") if c[k]))

    curing = [r for r in scored if r.get("curing")]
    if curing:
        went = sum(1 for r in curing if r.get("runout_on"))
        lines.append(f"  curing was flagged on {len(curing)}: {went} ran out inside "
                     f"the scoring horizon, {len(curing) - went} recovered")
    return "\n".join(lines)



def run(write: bool, wait_days: int, score_days: int, today: date | None = None) -> int:
    """Score every unscored forecast. Returns the process exit code."""
    today = today or date.today()
    params = {"runout": float(depletion.RUNOUT_PCT), "score_days": score_days}
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(SCAN_SQL, params)
                rows = cur.fetchall()
            rows = [dict(r) if not isinstance(r, dict) else r for r in rows]

            scored: list[dict] = []
            pending = 0
            for row in rows:
                outcome, runout = verdict(row, today, wait_days, score_days)
                if outcome is None:
                    pending += 1
                    continue
                scored.append({**row, "outcome": outcome, "runout_on": runout})
                if write:
                    with conn.cursor() as cur:
                        cur.execute(SCORE_SQL, {"id": row["id"], "outcome": outcome,
                                                "runout_on": runout})
    except Exception:  # noqa: BLE001
        log.exception("backtest could not run")
        return 1

    if not scored and not pending:
        print("no unscored forecasts - nothing to judge")
        return 0
    print(summarise(scored, pending))
    if not write and scored:
        print("\n(dry run - re-run with --write to store these verdicts)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="store the verdicts (default: report only)")
    ap.add_argument("--wait-days", type=int, default=30,
                    help="days to wait for a run-out before calling it no_collapse")
    ap.add_argument("--score-days", type=int, default=90,
                    help="furthest run-out we still attribute to this forecast")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return run(args.write, max(1, args.wait_days), max(7, args.score_days))


if __name__ == "__main__":
    raise SystemExit(main())
