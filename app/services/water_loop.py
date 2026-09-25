"""The water loop — turning one herder's tap into something useful for his neighbours.

Migration 009 gave a herder a way to tell us a water point's status. This module
closes the loop around that single tap:

  * QUEUE     — after the status report we ask one more digit: is the line short
                or long? A full tank with 40 herds queued is not the same
                decision as a full tank with nobody there.
  * FAN-OUT   — the other herders who drink from the SAME point are told what the
                reporter just confirmed, anonymously, with a cooldown so this
                never becomes a broadcast channel.
  * REPAIR    — "imekarabatiwa" ends a dry/broken report early (a pump that is
                fixed should not stay suppressed for 60 days) and tells the
                herders who were warned that it is back.

Design rules, same as the rest of the system:
  * One tap, never a form. Digits only, and a bare digit is only treated as an
    answer when we just asked the question (see the message router).
  * Honesty: we never say "there is water" — we say who confirmed what, and when.
  * Fail-open: a failure to notify anyone must never break the reply the reporter
    is owed.
  * Pure logic (message building, thresholds, queue parsing) is separated from
    the DB helpers so it is unit-testable without a database.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger(__name__)

QUEUE_UNKNOWN = "unknown"
QUEUE_SHORT = "short"      # no real wait — water straight away
QUEUE_LONG = "long"        # a real line of herds

QUEUE_LEVELS = (QUEUE_UNKNOWN, QUEUE_SHORT, QUEUE_LONG)

# The queue is the SECOND question, so it cannot reuse the status digits (1-5).
QUEUE_DIGITS = {"1": QUEUE_SHORT, "2": QUEUE_LONG}

# A queue reading is about "now": after this it stops being shown.
QUEUE_FRESH_HOURS = 24

# Never tell the same herder about the same point more than once in this window.
FANOUT_COOLDOWN_HOURS = 12

# "Fix it" reports from the field.
REPAIR_KEYWORDS = (
    "imekarabatiwa", "karabati", "repaired", "fixed", "imerekebishwa",
    "inafanya kazi sasa", "imeanza kufanya kazi", "maji yamerudi",
    "water is back", "pampu inafanya kazi",
)


def queue_for_digit(text: str) -> Optional[str]:
    """The queue level implied by a bare '1'/'2' reply (None for anything else).

    Only used when we have just asked the queue question — '1' means something
    different in every other menu, which is why the caller checks its own state
    before asking this.
    """
    return QUEUE_DIGITS.get((text or "").strip().lower())


def queue_question(lang: str = "swa") -> str:
    """The one-tap queue question (asked straight after a status report)."""
    if lang in ("swa", "swahili"):
        return ("Foleni ya wanyama ikoje?\n1 fupi — ninachota haraka\n"
                "2 ndefu — nasubiri sana")
    return ("How long is the queue of animals?\n1 short — I draw water quickly\n"
            "2 long — I wait a long time")


def queue_label(level: Optional[str], lang: str = "swa") -> str:
    table_swa = {QUEUE_SHORT: "foleni fupi", QUEUE_LONG: "foleni ndefu",
                 QUEUE_UNKNOWN: "foleni haijulikani"}
    table_eng = {QUEUE_SHORT: "short queue", QUEUE_LONG: "long queue",
                 QUEUE_UNKNOWN: "queue unknown"}
    return (table_swa if lang in ("swa", "swahili") else table_eng).get(
        level or QUEUE_UNKNOWN, table_swa[QUEUE_UNKNOWN])


def queue_sentence(level: Optional[str], lang: str = "swa", name: str | None = None) -> str:
    """A complete sentence about the queue (never a bare fragment)."""
    where = f" kwenye {name}" if (name and lang in ("swa", "swahili")) else (
        f" at {name}" if name else "")
    if lang in ("swa", "swahili"):
        if level == QUEUE_SHORT:
            return f"Foleni{where} ni fupi."
        if level == QUEUE_LONG:
            return f"Foleni{where} ni ndefu."
        return f"Hatujui urefu wa foleni{where}."
    if level == QUEUE_SHORT:
        return f"The queue{where} is short."
    if level == QUEUE_LONG:
        return f"The queue{where} is long."
    return f"We do not know the queue{where}."


def thanks_for_queue(level: str, lang: str = "swa") -> str:
    """Acknowledgement for the queue answer — short, and never a nag."""
    if lang in ("swa", "swahili"):
        return (f"Asante! Tumerekodi {queue_label(level, 'swa')}.\n"
                "Taarifa hii inasaidia wachungaji wengine wa chanzo hicho.")
    return (f"Thank you! We recorded a {queue_label(level, 'eng')}.\n"
            "This helps the other herders who use that point.")


def queue_is_fresh(updated_at: Optional[datetime], now: Optional[datetime] = None) -> bool:
    """True when a stored queue reading is recent enough to show."""
    if updated_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return (now - updated_at) <= timedelta(hours=QUEUE_FRESH_HOURS)


def should_fanout(status: str, previous_status: Optional[str],
                  last_notice_at: Optional[datetime] = None,
                  now: Optional[datetime] = None) -> bool:
    """Should this status be pushed to the other herders on the same point?

    Deliberately conservative — a noisy network gets muted:
      * never more than once per FANOUT_COOLDOWN_HOURS per point,
      * only when the status CHANGED, or when it is a first hard verdict,
      * never for 'unknown' (nothing to say).
    """
    if not status or status == "unknown":
        return False
    now = now or datetime.now(timezone.utc)
    if last_notice_at is not None:
        if last_notice_at.tzinfo is None:
            last_notice_at = last_notice_at.replace(tzinfo=timezone.utc)
        if (now - last_notice_at) < timedelta(hours=FANOUT_COOLDOWN_HOURS):
            return False
    # Same verdict as the one already stored means nobody has news.
    return not (previous_status and previous_status == status)


def fanout_message(status: str, name: str | None, lang: str = "swa",
                   queue: Optional[str] = None) -> str:
    """What the OTHER herders on the same point are told.

    Anonymous by design (no phone numbers, no names of reporters) and never a
    claim of our own: we report what a herder confirmed, in a full sentence.
    """
    from app.services import water_status

    point = name or ("chanzo chako" if lang in ("swa", "swahili") else "your water point")
    state = water_status.status_label(status, "swa" if lang in ("swa", "swahili") else "eng")
    queue_line = ""
    if queue and queue != QUEUE_UNKNOWN:
        queue_line = ("\n" + queue_sentence(queue, lang, name=None).strip())
    if lang in ("swa", "swahili"):
        return (f"💧 Mchungaji mwenzio amethibitisha: {point} {state}."
                f"{queue_line}\nTaarifa hii inatoka kwa mchungaji wa eneo lako.")
    return (f"💧 Another herder has confirmed: {point} is {state}."
            f"{queue_line}\nThis report comes from a herder in your area.")


def repair_message(name: str | None, lang: str = "swa") -> str:
    """Told to the herders who had been warned, once a point is working again."""
    point = name or ("chanzo" if lang in ("swa", "swahili") else "the point")
    if lang in ("swa", "swahili"):
        return (f"✅ {point} imekarabatiwa na maji yamerudi.\n"
                "Asante kwa taarifa zenu.")
    return (f"✅ {point} has been repaired and there is water again.\n"
            "Thank you for your reports.")


def repair_confirmed(lang: str = "swa") -> str:
    """Acknowledgement for the herder who reported the repair."""
    if lang in ("swa", "swahili"):
        return ("Asante! Tumerekodi kuwa chanzo kimekarabatiwa na maji yapo tena.\n"
                "Taarifa yako inasaidia wachungaji wengine.")
    return ("Thank you! We recorded that the point is repaired and has water again.\n"
            "Your report helps other herders.")


# --- DB helpers -------------------------------------------------------------
# Imported lazily so the pure logic above stays importable (and testable)
# without a database or any environment variables.

_SNAPSHOT_SQL = """
select ws.status, ws.status_updated_at, ws.queue_level, ws.queue_updated_at,
       ws.name,
       (select max(n.created_at) from water_notices n
         where n.water_source_id = ws.id and n.kind = 'status_fanout') as last_notice_at
from water_sources ws
where ws.id = %(water_source_id)s
"""

_PEERS_SQL = """
select phone_number
from pastoralists
where water_source_id = %(water_source_id)s
  and phone_number <> %(exclude_phone)s
"""

_NOTICE_SQL = """
insert into water_notices (water_source_id, phone, kind, status)
values (%(water_source_id)s, %(phone)s, %(kind)s, %(status)s)
"""

_TOLD_BROKEN_SQL = """
select distinct phone
from water_notices
where water_source_id = %(water_source_id)s
  and kind = 'status_fanout'
  and status in ('dry','broken','not_found')
  and created_at > now() - interval '%(days)s days'
"""

_SET_QUEUE_SQL = """
update water_sources
set queue_level = %(level)s,
    queue_updated_at = now(),
    queue_reports = queue_reports + 1
where id = %(water_source_id)s
"""

_QUEUE_REPORT_SQL = """
insert into ground_truth_reports
    (pastoralist_id, water_source_id, report_type, report_text, queue_level)
values (%(pastoralist_id)s, %(water_source_id)s, 'other', %(report_text)s, %(level)s)
"""

_SET_REPAIRED_SQL = """
update water_sources
set status = 'functional',
    status_updated_at = now(),
    status_source = 'herder',
    status_reports = status_reports + 1,
    repaired_at = now(),
    confidence = least(confidence * 1.1 + 0.05, 0.99),
    last_confirmed = now()
where id = %(water_source_id)s
"""

_REPAIR_REPORT_SQL = """
insert into ground_truth_reports
    (pastoralist_id, water_source_id, report_type, report_text)
values (%(pastoralist_id)s, %(water_source_id)s, 'water_repaired', %(report_text)s)
"""

_QUEUE_FOR_ADVISORY_SQL = """
select queue_level, queue_updated_at
from water_sources
where id = %(water_source_id)s
"""


def point_snapshot(water_source_id: str) -> dict | None:
    """Current status/queue/notice state of a point — read BEFORE the new report
    is written, so the fan-out can tell 'changed' from 'same as before'."""
    from app.db import get_pg_connection

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_SNAPSHOT_SQL, {"water_source_id": water_source_id})
            row = cur.fetchone()
    return dict(row) if row else None


def record_queue(water_source_id: str, level: str, pastoralist_id: str | None,
                 raw_text: str = "") -> None:
    """Store the queue reading (point-level, plus the report row it came from)."""
    from app.db import get_pg_connection

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_SET_QUEUE_SQL, {"water_source_id": water_source_id,
                                         "level": level})
            if pastoralist_id:
                cur.execute(_QUEUE_REPORT_SQL,
                            {"pastoralist_id": pastoralist_id,
                             "water_source_id": water_source_id,
                             "report_text": raw_text,
                             "level": level})
        conn.commit()
    log.info("Queue recorded level=%s water_source_id=%s", level, water_source_id)


def queue_for_advisory(water_source_id: str) -> tuple[Optional[str], Optional[datetime]]:
    """(queue_level, updated_at) for a point — used when building guidance."""
    from app.db import get_pg_connection

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_QUEUE_FOR_ADVISORY_SQL, {"water_source_id": water_source_id})
            row = cur.fetchone()
    if not row:
        return None, None
    return row["queue_level"], row["queue_updated_at"]


def peers_for_point(water_source_id: str, exclude_phone: str) -> list[str]:
    """The other herders who drink from this point (their confirmed water point)."""
    from app.db import get_pg_connection

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_PEERS_SQL, {"water_source_id": water_source_id,
                                     "exclude_phone": exclude_phone})
            rows = cur.fetchall()
    return [r["phone_number"] for r in rows]


def log_notice(water_source_id: str, phone: str, kind: str,
               status: str | None = None) -> None:
    """Record that we told this phone about this point (also the cooldown)."""
    from app.db import get_pg_connection

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_NOTICE_SQL, {"water_source_id": water_source_id,
                                      "phone": phone, "kind": kind,
                                      "status": status})
        conn.commit()


def phones_told_it_was_broken(water_source_id: str, days: int = 60) -> list[str]:
    """Everyone we warned about this point, so a repair can reach them too."""
    from app.db import get_pg_connection

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_TOLD_BROKEN_SQL, {"water_source_id": water_source_id,
                                           "days": int(days)})
            rows = cur.fetchall()
    return [r["phone"] for r in rows]


def record_repair(water_source_id: str, pastoralist_id: str | None,
                  raw_text: str = "") -> None:
    """A repaired point comes back to life NOW.

    Without this, a pump fixed the day after it was reported broken would stay
    suppressed for the whole STALE_STATUS_DAYS window — herders would walk past a
    working pump because *our* record said it was dead, which is exactly the kind
    of quiet wrongness this project must not ship.
    """
    from app.db import get_pg_connection

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_SET_REPAIRED_SQL, {"water_source_id": water_source_id})
            if pastoralist_id:
                cur.execute(_REPAIR_REPORT_SQL,
                            {"pastoralist_id": pastoralist_id,
                             "water_source_id": water_source_id,
                             "report_text": raw_text})
        conn.commit()
    log.info("Repair recorded water_source_id=%s", water_source_id)


def looks_like_repair(text_lower: str) -> bool:
    """Does this message say the point is working again?"""
    t = (text_lower or "").strip()
    return any(k in t for k in REPAIR_KEYWORDS)


__all__ = [
    "QUEUE_UNKNOWN", "QUEUE_SHORT", "QUEUE_LONG", "QUEUE_LEVELS", "QUEUE_DIGITS",
    "QUEUE_FRESH_HOURS", "FANOUT_COOLDOWN_HOURS", "REPAIR_KEYWORDS",
    "queue_for_digit", "queue_question", "queue_label", "queue_sentence",
    "thanks_for_queue", "queue_is_fresh", "should_fanout", "fanout_message",
    "repair_message", "repair_confirmed", "looks_like_repair",
    "point_snapshot", "record_queue", "queue_for_advisory", "peers_for_point",
    "log_notice", "phones_told_it_was_broken", "record_repair",
]



