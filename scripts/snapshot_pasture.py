"""Snapshot the pasture in every ring, and warn the herders whose grass is going.

TWO JOBS, ONE PASS
------------------
1. HISTORY. For every water point a herder actually uses, read the stored satellite
   overview for that species' ring and keep one row per point/species/day
   (ring_pasture_samples). This is the only way a depletion DATE can ever exist: a
   single image cannot show a trend, and the COGs themselves are refreshed every 14
   days, so week-by-week samples are what turn "the grass is thin" into "about 3
   weeks".

2. THE WARNING. Fit the history (app/services/depletion.py), and when it projects a
   run-out inside the next ~8 weeks, send the herder the date range and one action.
   A ring that is already spent gets urgency today instead of arithmetic.

HONESTY AND SAFETY (why the flags exist)
----------------------------------------
  * DRY RUN IS THE DEFAULT. Nothing is sent unless --send is passed; the exact text
    per herder is printed first. A job that can message hundreds of phones should
    never do it by accident.
  * WhatsApp's 24-HOUR WINDOW is respected (weekly_note.can_message_now): outside it
    the herder is counted as 'outside_24h_window' rather than messaged into the void.
  * ONE WARNING PER DIVERGENCE. depletion_alerts is keyed on (point, species, the
    week the run-out starts), so a re-run, a retry and the schedule cannot
    triple-message anyone — but a forecast that moves materially closer is a new
    warning, because that is new information.
  * NO HISTORY, NO MESSAGE. While the trend is still being measured the herder is
    told nothing (he can ask any time); a made-up date is worse than silence.

Usage:
  python scripts/snapshot_pasture.py                 # dry run: record + print
  python scripts/snapshot_pasture.py --send          # record + send (in-window)
  python scripts/snapshot_pasture.py --send --limit 5
  python scripts/snapshot_pasture.py --no-record     # forecast from history only
  python scripts/snapshot_pasture.py --phone 2547... # one herder
  python scripts/snapshot_pasture.py --all           # warn even beyond the horizon
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.services import (  # noqa: E402
    depletion,
    features,
    pasture_history,
    weekly_note,
    whatsapp_client,
)
from app.services.pastoralists import list_note_recipients  # noqa: E402

log = logging.getLogger("snapshot_pasture")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--send", action="store_true",
                        help="actually send (default: dry run, print only)")
    parser.add_argument("--limit", type=int, default=0,
                        help="cap the number of herders messaged (0 = all)")
    parser.add_argument("--phone", default=None, help="only this phone number")
    parser.add_argument("--no-record", action="store_true",
                        help="do not store a new sample (fit the stored history only)")
    parser.add_argument("--all", action="store_true",
                        help="print/alert every projected ring, not only those inside 60 days")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:  # emoji in a dry run on a cp1252 console must not crash the run
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    if not features.is_enabled("depletion"):
        log.warning("the depletion feature is switched off (ARDA_FEATURES) — nothing to do")
        return 0

    recipients = list_note_recipients()
    if args.phone:
        recipients = [r for r in recipients if r["phone_number"] == args.phone]
    if args.limit:
        recipients = recipients[:args.limit]
    log.info("pasture snapshot: %d herder(s), send=%s, record=%s",
             len(recipients), args.send, not args.no_record)

    # One satellite read per (point, species, interval): three herders at the same
    # borehole must not trigger three downloads of the same 8 MB overview.
    groups: dict[tuple, list[dict]] = {}
    for r in recipients:
        key = (r["water_source_id"], r.get("species") or "cattle",
               r.get("water_interval") or "daily")
        groups.setdefault(key, []).append(r)

    sent = skipped_window = no_alert = failed = 0
    for (point_id, species, interval), herders in groups.items():
        place = herders[0].get("point_name") or herders[0].get("ward")
        try:
            dep = pasture_history.forecast_for(
                point_id, species, interval=interval, record=not args.no_record)
        except Exception:  # noqa: BLE001
            log.exception("forecast failed for %s/%s (skipped)", point_id, species)
            failed += len(herders)
            continue
        if dep is None:
            log.info("%s/%s: no satellite reading — no history, no message", point_id, species)
            no_alert += len(herders)
            continue
        if not (dep.is_alert or args.all):
            log.info("%s/%s: %s (usable %s%%) — nothing to warn about",
                     point_id, species, dep.status, dep.usable_pct)
            no_alert += len(herders)
            continue

        week = pasture_history.runout_week(dep)
        if week is not None and pasture_history.already_alerted(point_id, species, week):
            log.info("%s/%s: already warned about the week of %s — not repeating",
                     point_id, species, week)
            no_alert += len(herders)
            continue

        delivered_here = 0
        for r in herders:
            lang = "swahili" if r["preferred_language"] == "swahili" else "english"
            body = depletion.alert_message(dep, lang, place)
            if not body:
                no_alert += 1
                continue
            if not args.send:
                print(f"\n--- {r['phone_number']} ({place}) {species} "
                      f"{dep.status} {dep.days_left_lo}-{dep.days_left_hi}d ---\n{body}")
                continue
            if not weekly_note.can_message_now(r.get("last_inbound_hours")):
                skipped_window += 1
                continue
            try:
                whatsapp_client.send_text(r["phone_number"], body)
                delivered_here += 1
                sent += 1
            except Exception:  # noqa: BLE001
                log.exception("depletion alert failed for %s (non-fatal)",
                              r["phone_number"])
                failed += 1

        # Claim the divergence only after at least one herder actually got it: a run
        # where everyone was outside the 24-hour window must be retried, not marked
        # as done.
        if args.send and delivered_here and week is not None:
            pasture_history.record_alert(point_id, species, week)

    log.info("pasture snapshot done: sent=%d skipped_outside_window=%d no_alert=%d failed=%d",
             sent, skipped_window, no_alert, failed)
    if not args.send:
        log.info("dry run only — samples were stored; re-run with --send to deliver")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
