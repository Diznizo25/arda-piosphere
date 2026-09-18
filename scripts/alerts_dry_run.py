"""
Show exactly who WOULD be alerted, and why, without sending anything.

This is the gate before alerting goes live. Read the output, decide whether you
would be happy for every one of those messages to land on a real phone, and only
then set ALERTS_ENABLED.

    python scripts/alerts_dry_run.py                 # every currently-unusable point
    python scripts/alerts_dry_run.py --water-source <uuid>
    python scripts/alerts_dry_run.py --since 7       # points that changed in 7 days

Nothing here sends. `dry_run=True` is passed all the way down, and the rows land
in alert_log with status='dry_run' so the suppression reasons are auditable too.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_pg_connection  # noqa: E402
from app.services import alerts  # noqa: E402

RECENT_SQL = """
select ws.id, ws.name, ws.water_type, ws.status, ws.status_reports,
       ws.status_updated_at,
       (select count(*) from pastoralists p where p.water_source_id = ws.id) as relies_on
from water_sources ws
where ws.status in ('dry', 'broken', 'not_found')
  and (%(since_days)s::int is null
       or ws.status_updated_at > now() - (%(since_days)s || ' days')::interval)
  and (%(water_source_id)s::uuid is null or ws.id = %(water_source_id)s)
order by ws.status_updated_at desc nulls last
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--water-source", default=None)
    ap.add_argument("--since", type=int, default=None,
                    help="Only points whose status changed in the last N days")
    args = ap.parse_args()

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(RECENT_SQL, {"since_days": args.since,
                                     "water_source_id": args.water_source})
            points = cur.fetchall()

    if not points:
        print("No water points currently reported dry/broken/not-found.")
        return

    print(f"ALERTS_ENABLED={'yes' if alerts.alerts_enabled() else 'NO (kill switch off)'}")
    print(f"cooldown={alerts.ALERT_COOLDOWN_DAYS}d  "
          f"max/herder/day={alerts.MAX_ALERTS_PER_HERDER_PER_DAY}  "
          f"freeform window={alerts.FREEFORM_WINDOW_HOURS}h\n")

    totals: dict[str, int] = {}
    for p in points:
        label = p["name"] or p["water_type"] or "unnamed"
        print(f"── {label}  [{p['status']}]  {p['status_reports']} report(s)  "
              f"· {p['relies_on']} herder(s) rely on it")
        decisions = alerts.notify_water_status_change(
            str(p["id"]), p["status"], reporter_pastoralist_id=None, dry_run=True)
        if not decisions:
            print("     (nobody has confirmed this as their water point)\n")
            continue
        for d in decisions:
            mark = "SEND" if d.would_send else "skip"
            print(f"     [{mark}] {d.phone[-4:]:>4}  {d.status:<20} {d.detail or ''}")
            if d.would_send and d.message:
                for line in d.message.splitlines():
                    print(f"            | {line}")
        for k, v in alerts.summarise(decisions).items():
            totals[k] = totals.get(k, 0) + v
        print()

    print("TOTAL:", totals or "nothing")
    if totals.get("suppressed_window"):
        print(f"\n{totals['suppressed_window']} herder(s) could not be reached because "
              f"they have not messaged in {alerts.FREEFORM_WINDOW_HOURS}h.\n"
              f"That number is the argument for submitting WhatsApp templates (Phase 3b).")


if __name__ == "__main__":
    main()
