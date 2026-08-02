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

log = logging.getLogger(__name__)

KEYWORDS = {
    "water_dry": ["water dry", "dry water", "no water", "maji hayupo", "haujui maji",
                  "bishaan hin jiru", "bishaan gogaa"],
    "water_available": ["water available", "water is there", "maji yapo", "bishaan jira"],
    "pasture_good": ["good pasture", "grass good", "malisho mazuri", "margi gaarii"],
    "pasture_poor": ["poor pasture", "no grass", "malisho mabaya", "margi hin jiru"],
}

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

BUMP_CONFIDENCE_DOWN_SQL = """
update water_sources
set confidence = greatest(confidence * 0.7, 0.1)
where id = %(water_source_id)s
"""

BUMP_CONFIDENCE_UP_SQL = """
update water_sources
set confidence = least(confidence * 1.1 + 0.05, 0.99),
    last_confirmed = now()
where id = %(water_source_id)s
"""


def parse_ground_truth_intent(text_lower: str) -> str | None:
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
    if water_source_id is None and report_type in ("water_dry", "water_available"):
        water_source_id = _find_nearest_water_source_id(pastoralist.id)

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
            if water_source_id and report_type == "water_dry":
                cur.execute(BUMP_CONFIDENCE_DOWN_SQL, {"water_source_id": water_source_id})
            elif water_source_id and report_type == "water_available":
                cur.execute(BUMP_CONFIDENCE_UP_SQL, {"water_source_id": water_source_id})
        conn.commit()

    log.info(f"Recorded ground truth report_type={report_type} pastoralist={pastoralist.phone_number} "
              f"water_source_id={water_source_id}")
