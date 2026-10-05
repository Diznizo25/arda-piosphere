"""
Manyatta registry + the grazing ledger's persistence (migration 014).

Two jobs:

  manyattas — the homestead the herd walks out from. Registered ONCE, from the
      herder's own location pin, and reused forever after. Every later answer
      measures the walk from here, which is what makes "we remember" structural
      rather than a promise: there is no code path that asks him twice.

  grazing_events — one row per "where we grazed today", kept as an audit trail.
      A band we showed a herder has to stay arguable afterwards (a wrong distance
      or a wrong biomass must be reconstructible), and the pile of these rows is
      the only calibration set for the biomass transfer function that will ever
      exist without a research project.

Everything is fail-open: a bookkeeping failure must never stop the answer, because
the herder asked a question and a table is not his problem.
"""
from __future__ import annotations

import logging

from app.db import get_pg_connection

log = logging.getLogger(__name__)

MANYATTA_SELECT = """
select m.id, m.name, m.phone, m.ward, m.county, m.source,
       st_x(m.geom) as lon, st_y(m.geom) as lat
from manyattas m
where m.phone = %(phone)s
order by m.updated_at desc
limit 1
"""

MANYATTA_UPSERT = """
insert into manyattas (phone, name, geom, ward, county, source)
values (%(phone)s, %(name)s, st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326),
        %(ward)s, %(county)s, %(source)s)
returning id, st_x(geom) as lon, st_y(geom) as lat, name, ward, county, source
"""

MANYATTA_UPDATE = """
update manyattas
set geom = st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326),
    name = coalesce(%(name)s, name),
    source = %(source)s,
    updated_at = now()
where id = %(id)s
returning id, st_x(geom) as lon, st_y(geom) as lat, name, ward, county, source
"""

SET_HERDER_MANYATTA = """
update pastoralists set manyatta_id = %(manyatta_id)s where phone_number = %(phone)s
"""

INSERT_EVENT = """
insert into grazing_events (
    phone, pastoralist_id, manyatta_id, water_source_id, species, head_count,
    geom, source, walk_km, origin_km, from_manyatta, snapshot_as_of, forage_class,
    biomass_lo_kg_ha, biomass_hi_kg_ha, utilisable_lo_kg_ha, utilisable_hi_kg_ha,
    me_mj_per_kg_dm, required_mj, intake_mj, balance_mj, balance_lo_mj,
    balance_hi_mj, verdict, advice_code, supplement_kg_per_head, lang)
values (
    %(phone)s, %(pastoralist_id)s, %(manyatta_id)s, %(water_source_id)s,
    %(species)s, %(head_count)s,
    st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326), %(source)s,
    %(walk_km)s, %(origin_km)s, %(from_manyatta)s, %(snapshot_as_of)s,
    %(forage_class)s, %(biomass_lo_kg_ha)s, %(biomass_hi_kg_ha)s,
    %(utilisable_lo_kg_ha)s, %(utilisable_hi_kg_ha)s, %(me_mj_per_kg_dm)s,
    %(required_mj)s, %(intake_mj)s, %(balance_mj)s, %(balance_lo_mj)s,
    %(balance_hi_mj)s, %(verdict)s, %(advice_code)s, %(supplement_kg_per_head)s,
    %(lang)s)
returning id
"""

DUPLICATE_CHECK = """
select id, created_at,
       extract(epoch from (now() - created_at)) / 60.0 as minutes_ago
from grazing_events
where phone = %(phone)s
order by created_at desc
limit 1
"""

SET_EVENT_QUALITY = """
update grazing_events
set herder_quality = %(quality)s
where id = (
    select id from grazing_events
    where phone = %(phone)s and herder_quality is null
    order by created_at desc
    limit 1
)
"""


def manyatta_for_phone(phone: str) -> dict | None:
    """The herder's registered homestead, or None (fail-open)."""
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(MANYATTA_SELECT, {"phone": phone})
                row = cur.fetchone()
        return dict(row) if row else None
    except Exception:  # noqa: BLE001
        log.exception("manyatta lookup failed (non-fatal)")
        return None


def register_manyatta(phone: str, lat: float, lon: float, *,
                      name: str | None = None, source: str = "pin",
                      ward: str | None = None, county: str = "Isiolo") -> dict | None:
    """Register (or gently move) the herder's manyatta. Returns the row or None."""
    existing = manyatta_for_phone(phone)
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                if existing:
                    cur.execute(MANYATTA_UPDATE, {
                        "id": existing["id"], "lon": lon, "lat": lat,
                        "name": name, "source": source})
                else:
                    cur.execute(MANYATTA_UPSERT, {
                        "phone": phone, "name": name, "lon": lon, "lat": lat,
                        "source": source, "ward": ward, "county": county})
                row = cur.fetchone()
                if row and row.get("id"):
                    cur.execute(SET_HERDER_MANYATTA,
                                {"manyatta_id": row["id"], "phone": phone})
            conn.commit()
        return dict(row) if row else None
    except Exception:  # noqa: BLE001
        log.exception("manyatta registration failed (non-fatal)")
        return None


def recent_event_minutes(phone: str) -> tuple[str | None, float | None]:
    """(event_id, minutes_ago) of the herder's last grazing event (fail-open)."""
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(DUPLICATE_CHECK, {"phone": phone})
                row = cur.fetchone()
        if not row:
            return None, None
        return str(row["id"]), float(row["minutes_ago"])
    except Exception:  # noqa: BLE001
        log.debug("duplicate check failed (non-fatal)", exc_info=True)
        return None, None


def record_grazing_event(phone: str, *, lat: float, lon: float, source: str,
                         species: str | None, head_count: int | None,
                         advice=None, manyatta_id: str | None = None,
                         water_source_id: str | None = None,
                         pastoralist_id: str | None = None,
                         lang: str = "swa") -> str | None:
    """Persist one grazing event. Returns its id, or None when it could not be
    written — the herder still gets his answer either way."""
    q = getattr(advice, "quality", None)
    led = getattr(advice, "ledger", None)
    rec = getattr(advice, "recommended", None)
    params = {
        "phone": phone, "pastoralist_id": pastoralist_id,
        "manyatta_id": manyatta_id, "water_source_id": water_source_id,
        "species": species, "head_count": head_count, "lat": lat, "lon": lon,
        "source": source if source in ("pin", "map", "landmark") else "pin",
        "walk_km": getattr(advice, "walk_km", None),
        "origin_km": getattr(advice, "walk_km", None),
        "from_manyatta": bool(getattr(advice, "from_manyatta", False)),
        "snapshot_as_of": (q.snapshot_as_of if q else None) or None,
        "forage_class": (q.condition if q else None),
        "biomass_lo_kg_ha": (q.biomass_lo_kg_ha if q else None),
        "biomass_hi_kg_ha": (q.biomass_hi_kg_ha if q else None),
        "utilisable_lo_kg_ha": (q.utilisable_lo_kg_ha if q else None),
        "utilisable_hi_kg_ha": (q.utilisable_hi_kg_ha if q else None),
        "me_mj_per_kg_dm": (q.me_mj_per_kg_dm if q else None),
        "required_mj": (led.required_mj if led else None),
        "intake_mj": (led.intake_mj if led else None),
        "balance_mj": (led.balance_mj if led else None),
        "balance_lo_mj": (led.balance_lo_mj if led else None),
        "balance_hi_mj": (led.balance_hi_mj if led else None),
        "verdict": (led.verdict if led else None),
        "advice_code": (rec.code if rec else "none"),
        "supplement_kg_per_head": (rec.kg_per_head if rec else None),
        "lang": lang,
    }
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(INSERT_EVENT, params)
                row = cur.fetchone()
            conn.commit()
        return str(row["id"]) if row else None
    except Exception:  # noqa: BLE001
        log.exception("grazing event insert failed (non-fatal)")
        return None


def set_event_quality(phone: str, quality: str) -> None:
    """Attach the herder's one-tap answer to his most recent ungraded event."""
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(SET_EVENT_QUALITY, {"phone": phone, "quality": quality})
            conn.commit()
    except Exception:  # noqa: BLE001
        log.exception("grazing quality update failed (non-fatal)")


def recent_events(phone: str, limit: int = 5) -> list[dict]:
    """The herder's last few walks (used by the weekly line and the dev probe)."""
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """select created_at, walk_km, forage_class, verdict, balance_mj,
                              advice_code, herder_quality
                       from grazing_events where phone = %(phone)s
                       order by created_at desc limit %(limit)s""",
                    {"phone": phone, "limit": limit})
                return [dict(r) for r in cur.fetchall()]
    except Exception:  # noqa: BLE001
        log.debug("recent grazing events unavailable", exc_info=True)
        return []
