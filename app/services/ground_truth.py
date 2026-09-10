"""
Minimal ground-truth feedback loop (build step 9): a herder's free-text WhatsApp
reply ("maji hayupo" / "water point dry" / etc.) gets classified into a
report_type, written to ground_truth_reports, and — when it's about water —
used to nudge water_sources.confidence and last_confirmed for the nearest
known water point to where the herder last shared their location.

Keyword matching here is intentionally simple for Phase 1 (start minimal,
expand later per the spec) — not NLP/LLM classification.
"""
from __future__ import annotations

import logging

from app.db import get_pg_connection
from app.services import water_status

log = logging.getLogger(__name__)

KEYWORDS = {
    "water_dry": ["water dry", "dry water", "no water", "maji hayupo", "haujui maji",
                  "bishaan hin jiru", "bishaan gogaa"],
    "water_available": ["water available", "water is there", "maji yapo", "bishaan jira"],
    "water_flowing": ["maji yanatiririka", "water flowing", "inatiririka"],
    "water_intermittent": ["maji ya vipindi", "seasonal water", "intermittent"],
    "water_broken": ["pump broken", "broken pump", "imeharibika", "pampu imeharibika",
                     "bomba limeharibika", "haina pampu"],
    "water_not_found": ["haipo tena", "no longer there", "not there any more",
                        "hakuna maji hapa", "does not exist", "haipo"],
    "pasture_good": ["good pasture", "grass good", "malisho mazuri", "margi gaarii"],
    "pasture_poor": ["poor pasture", "no grass", "malisho mabaya", "margi hin jiru"],
}

# Confirmed-good statuses raise confidence; unusable ones cut it hard; intermittent
# leaves confidence alone (the point is real, it just dries out).
_CONFIDENCE_UP = (water_status.STATUS_FLOWING, water_status.STATUS_FUNCTIONAL)
_CONFIDENCE_DOWN = water_status.UNUSABLE_STATUSES

FIND_NEAREST_TO_LOCATION_SQL = """
select ws.id, ws.confidence
from water_sources ws
join pastoralists p on p.id = %(pastoralist_id)s
where p.last_known_location is not null
order by ws.geom <-> p.last_known_location
limit 1
"""

INSERT_REPORT_SQL = """
insert into ground_truth_reports (pastoralist_id, water_source_id, report_type, report_text)
values (%(pastoralist_id)s, %(water_source_id)s, %(report_type)s, %(report_text)s)
"""

SET_WATER_STATUS_SQL = """
update water_sources
set status = %(status)s,
    status_updated_at = now(),
    status_source = 'herder',
    status_reports = status_reports + 1,
    confidence = case
        when %(direction)s = 'up' then least(confidence * 1.1 + 0.05, 0.99)
        when %(direction)s = 'down' then greatest(confidence * 0.7, 0.1)
        else confidence end,
    last_confirmed = case when %(direction)s = 'up' then now() else last_confirmed end
where id = %(water_source_id)s
"""


def _confidence_direction(status: str) -> str:
    if status in _CONFIDENCE_UP:
        return "up"
    if status in _CONFIDENCE_DOWN:
        return "down"
    return "hold"


def parse_ground_truth_intent(text_lower: str) -> str | None:
    """One-tap digit first (that is how herders actually answer), then keywords."""
    digit = water_status.intent_for_digit(text_lower)
    if digit is not None:
        return digit
    for report_type, phrases in KEYWORDS.items():
        if any(p in text_lower for p in phrases):
            return report_type
    return None


def _find_nearest_water_source_id(pastoralist_id: str) -> str | None:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(FIND_NEAREST_TO_LOCATION_SQL, {"pastoralist_id": pastoralist_id})
            row = cur.fetchone()
    return str(row["id"]) if row else None


def record_ground_truth(pastoralist, report_type: str, raw_text: str,
                         water_source_id: str | None = None) -> None:
    """Store the herder's report and, when it is about water, write the water
    point's STATUS (which then gates guidance: dry/broken/not-found points stop
    being recommended for STALE_STATUS_DAYS)."""
    if water_source_id is None and report_type in (
            "water_dry", "water_available", "water_flowing", "water_intermittent",
            "water_broken", "water_not_found"):
        water_source_id = _find_nearest_water_source_id(pastoralist.id)

    status = water_status.status_for_report(report_type)
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                INSERT_REPORT_SQL,
                {
                    "pastoralist_id": pastoralist.id,
                    "water_source_id": water_source_id,
                    "report_type": report_type,
                    "report_text": raw_text,
                },
            )
            if water_source_id and status:
                cur.execute(SET_WATER_STATUS_SQL, {
                    "water_source_id": water_source_id,
                    "status": status,
                    "direction": _confidence_direction(status),
                })
        conn.commit()

    log.info("Recorded ground truth report_type=%s pastoralist=%s water_source_id=%s status=%s",
             report_type, pastoralist.phone_number, water_source_id, status)


def water_status_for(water_source_id: str) -> dict | None:
    """Current status of a water point: {status, updated_at, source, reports}."""
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """select status, status_updated_at, status_source, status_reports
                   from water_sources where id = %(id)s""",
                {"id": water_source_id},
            )
            row = cur.fetchone()
    return dict(row) if row else None
