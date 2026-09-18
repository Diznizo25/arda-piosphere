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

import json
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
insert into ground_truth_reports (
    pastoralist_id, water_source_id, report_type, report_text,
    geom, indices_at_report, satellite_observed_at, water_source_distance_m)
values (
    %(pastoralist_id)s, %(water_source_id)s, %(report_type)s, %(report_text)s,
    case when %(lon)s::double precision is null then null
         else st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326) end,
    %(indices)s::jsonb, %(observed_at)s, %(distance_m)s)
"""

# Where the herder was, and what the satellite said about that exact spot.
GET_LABEL_CONTEXT_SQL = """
select ws.id as water_source_id,
       pz.last_computed,
       st_distance(ws.geom::geography,
                   st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326)::geography) as distance_m
from water_sources ws
join piosphere_zones pz
  on pz.water_source_id = ws.id and pz.species = 'camel'
order by ws.geom <-> st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326)
limit 1
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


def _label_context(pastoralist) -> dict:
    """Where the herder was and what the satellite said about that exact spot.

    This is what makes a report TRAINABLE. Without a location a report is not a
    label: herders are mobile, so "malisho mabaya" could mean anywhere within a
    day's walk. And the index vector is sampled at the herder's own pixel, not
    averaged over a 25 km ring that mostly describes somewhere else.

    Entirely fail-open. A report with no context is still worth storing — it
    just cannot train anything.
    """
    ctx = {"lon": None, "lat": None, "indices": None,
           "observed_at": None, "distance_m": None}
    try:
        from app.services import pastoralists as people

        loc = people.get_last_location(pastoralist.phone_number)
        if not loc:
            return ctx
        lon, lat = loc
        ctx["lon"], ctx["lat"] = lon, lat

        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(GET_LABEL_CONTEXT_SQL, {"lon": lon, "lat": lat})
                row = cur.fetchone()
        if not row:
            return ctx
        ctx["observed_at"] = row["last_computed"]
        ctx["distance_m"] = round(float(row["distance_m"]), 1)

        from app.services import raster_read

        indices = raster_read.read_point_indices(str(row["water_source_id"]), lon, lat)
        if indices:
            ctx["indices"] = json.dumps(indices)
    except Exception:  # noqa: BLE001
        log.debug("label context unavailable for report", exc_info=True)
    return ctx


def record_ground_truth(pastoralist, report_type: str, raw_text: str,
                         water_source_id: str | None = None) -> None:
    """Store the herder's report and, when it is about water, write the water
    point's STATUS (which then gates guidance: dry/broken/not-found points stop
    being recommended for STALE_STATUS_DAYS).

    Every report is also captured as a potential training label — see
    `_label_context`. config/advisory_thresholds.yaml calls its numbers
    "starting defaults, not validated against ground truth yet"; these rows are
    what eventually replaces the guess with a measurement.
    """
    if water_source_id is None and report_type in (
            "water_dry", "water_available", "water_flowing", "water_intermittent",
            "water_broken", "water_not_found"):
        water_source_id = _find_nearest_water_source_id(pastoralist.id)

    context = _label_context(pastoralist)
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
                    **context,
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
