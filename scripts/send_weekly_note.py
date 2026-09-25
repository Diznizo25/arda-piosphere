"""Send the weekly note to every herder whose week it is.

What a herder gets (same shape every week, see app/services/weekly_note.py):
the rain line, where it did and did not rain by place name, his water point's
status and queue, the pest check when a window is up, a live-map link, and one
one-tap question.

Design decisions worth knowing before running this:

  * DRY RUN IS THE DEFAULT. Nothing is sent unless --send is passed, and it prints
    the exact text per herder first. A retention job that can spam hundreds of
    phones on a typo is not a job worth having.
  * WhatsApp's 24-HOUR WINDOW is respected. A free-form business message is only
    allowed within 24h of the herder's own last message; outside it we record the
    herder as 'outside_24h_window' rather than sending into the void. Reaching that
    group needs one approved utility template — an ops task, not a code one.
  * ONE NOTE PER HERDER PER WEEK is enforced by a unique key in weekly_notes, so a
    retry, a manual run and the scheduled run cannot triple-message anyone.
  * The last thing sent is the water-status question, and we record it as the
    herder's last advisory point — otherwise his reply "2" (imekauka) would arrive
    with no point attached and be ignored.

Usage:
  python scripts/send_weekly_note.py                  # dry run, prints the notes
  python scripts/send_weekly_note.py --send           # send (in-window herders only)
  python scripts/send_weekly_note.py --send --limit 5 # cap the blast radius
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")

from app.db import get_pg_connection  # noqa: E402
from app.services import (  # noqa: E402
    environment,
    forecast as forecast_service,
    pests,
    water_loop,
    water_status,
    weekly_note,
    whatsapp_client,
)
from app.services.pastoralists import (  # noqa: E402
    list_note_recipients,
    set_last_advisory_water_source,
)

log = logging.getLogger("send_weekly_note")

RECORD_SQL = """
insert into weekly_notes (phone, week_start, delivered, skipped_reason, body)
values (%(phone)s, %(week_start)s, %(delivered)s, %(reason)s, %(body)s)
on conflict (phone, week_start) do update
set sent_at = now(),
    delivered = excluded.delivered,
    skipped_reason = excluded.skipped_reason,
    body = excluded.body
"""

SENT_THIS_WEEK_SQL = """
select skipped_reason, delivered from weekly_notes
where phone = %(phone)s and week_start = %(week_start)s
"""


def _monday(today: date | None = None) -> date:
    today = today or date.today()
    return today - timedelta(days=today.weekday())


def _water_line(recipient: dict, lang: str) -> str:
    """A full sentence about his water point, with how old the report is."""
    point_id = recipient["water_source_id"]
    status, updated = None, None
    try:
        from app.services.ground_truth import water_status_for

        info = water_status_for(point_id) or {}
        status, updated = info.get("status"), info.get("status_updated_at")
    except Exception:  # noqa: BLE001
        log.debug("water status read failed (non-fatal)", exc_info=True)
    sentence = water_status.status_sentence(status, lang)
    age = water_status.age_days(updated)
    sw = lang in ("swa", "swahili")
    if age is not None:
        n = round(age)
        sentence = (f"{sentence} Taarifa ya mwisho ilikuja siku {n} zilizopita." if sw
                    else f"{sentence} The last report came {n} days ago.")
    elif sw:
        sentence = f"{sentence} Bado hatuna taarifa kutoka kwa mchungaji yeyote."
    else:
        sentence = f"{sentence} No herder has reported on it yet."
    return sentence


def build_for(recipient: dict, lang: str) -> str:
    """The full note text for one herder (DB reads only, no raster, no network)."""
    point_id = recipient["water_source_id"]
    sw_lang = "swahili" if lang == "swa" else "english"
    lines: list[str] = []

    # 1. His own rain outlook (stored series + cached forecast).
    try:
        o = environment.outlook(point_id, window_days=30)
        rain = forecast_service.rain_line(o, sw_lang)
        if rain:
            lines.append(rain)
    except Exception:  # noqa: BLE001
        log.debug("rain line failed (non-fatal)", exc_info=True)

    # 2. Where it rained, in place names (never a shaded band: our rain data is a
    #    point value, and the words refresh twice a day).
    try:
        place_line = forecast_service.place_rain_line(
            environment.own_rain_7d(point_id),
            environment.nearby_rain(point_id, limit=3),
            sw_lang)
        if place_line:
            lines.append(place_line)
    except Exception:  # noqa: BLE001
        log.debug("place-rain line failed (non-fatal)", exc_info=True)

    # 3. The queue at his point, while it is still fresh.
    try:
        level, updated = water_loop.queue_for_advisory(point_id)
        if water_loop.queue_is_fresh(updated):
            lines.append(water_loop.queue_sentence(level, lang))
    except Exception:  # noqa: BLE001
        log.debug("queue line failed (non-fatal)", exc_info=True)

    # 4. The pest check, only when a window is up (silence is a feature).
    try:
        o = pests.outlook_for_point(point_id)
        line = pests.weekly_line(o, lang) if o else None
        if line:
            lines.append(line)
    except Exception:  # noqa: BLE001
        log.debug("pest line failed (non-fatal)", exc_info=True)

    map_url = None
    if recipient.get("lat") is not None and recipient.get("lon") is not None:
        from app.config import get_settings

        base = (get_settings().app_public_base_url or "").rstrip("/")
        # A relative link in a WhatsApp message is dead on arrival: only send the map
        # line when we know our public address (APP_PUBLIC_BASE_URL on Render).
        if base.startswith("http"):
            map_url = (f"{base}/mapview/?lat={recipient['lat']}&lon={recipient['lon']}"
                       f"&species={recipient['species']}&id={point_id}&lang={lang}")
        else:
            log.warning("APP_PUBLIC_BASE_URL is not set — sending the note without "
                        "the map link (a relative URL would be useless in WhatsApp)")

    return weekly_note.build_note(
        lang=lang,
        place=recipient.get("point_name") or recipient.get("ward"),
        water_line=_water_line(recipient, lang),
        lines=lines,
        question=water_status.check_question(lang),
        map_url=map_url,
    )


def _record(phone: str, week_start: date, delivered: bool, reason: str | None,
            body: str | None) -> None:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(RECORD_SQL, {"phone": phone, "week_start": week_start,
                                     "delivered": delivered, "reason": reason,
                                     "body": body})
        conn.commit()


def _already_handled(phone: str, week_start: date) -> str | None:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SENT_THIS_WEEK_SQL, {"phone": phone,
                                             "week_start": week_start})
            row = cur.fetchone()
    if not row:
        return None
    return "already_sent" if row["delivered"] else (row["skipped_reason"] or "seen")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true",
                        help="actually send (default: dry run, print only)")
    parser.add_argument("--limit", type=int, default=0,
                        help="cap the number of herders processed")
    parser.add_argument("--phone", help="only this herder (for a careful test)")
    parser.add_argument("--week", help="week start date (YYYY-MM-DD), for replays")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # Dry runs print emoji: a Windows console (cp1252) would crash on them, and a
    # dry run that crashes is a dry run nobody trusts. Linux runners are UTF-8.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    week_start = (date.fromisoformat(args.week) if args.week else _monday())

    recipients = list_note_recipients()
    if args.phone:
        recipients = [r for r in recipients if r["phone_number"] == args.phone]
    if args.limit:
        recipients = recipients[:args.limit]

    log.info("weekly note %s: %d recipient(s) (dry run=%s)",
             week_start, len(recipients), not args.send)

    sent = skipped_window = already = failed = 0
    for r in recipients:
        lang = "swa" if r["preferred_language"] == "swahili" else "eng"
        phone = r["phone_number"]
        try:
            body = build_for(r, lang)
        except Exception:  # noqa: BLE001
            log.exception("note build failed for %s (skipped)", phone)
            failed += 1
            continue

        if _already_handled(phone, week_start):
            already += 1
            continue

        if not args.send:
            print(f"\n--- {phone} ({r.get('point_name')}) ---\n{body}")
            continue

        if not weekly_note.can_message_now(r.get("last_inbound_hours")):
            skipped_window += 1
            _record(phone, week_start, False, "outside_24h_window", None)
            continue

        try:
            whatsapp_client.send_text(phone, body)
            # Bind his one-tap reply to THIS point, or "2" (imekauka) arrives with
            # no point attached and is ignored.
            set_last_advisory_water_source(phone, r["water_source_id"])
            _record(phone, week_start, True, None, body)
            sent += 1
        except Exception:  # noqa: BLE001
            log.exception("weekly note send failed for %s (non-fatal)", phone)
            _record(phone, week_start, False, "send_failed", body)
            failed += 1

    log.info("weekly note done: sent=%d skipped_outside_window=%d already=%d failed=%d",
             sent, skipped_window, already, failed)
    if not args.send:
        log.info("dry run only — re-run with --send to deliver (in-window herders)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

