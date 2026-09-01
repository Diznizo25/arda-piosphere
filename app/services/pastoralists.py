"""CRUD helpers for the `pastoralists` table, used by the WhatsApp handler to
track each herder's species/language/last-location across turns of the
conversation (WhatsApp has no built-in session state)."""
from __future__ import annotations

from dataclasses import dataclass

from app.db import get_pg_connection

GET_SQL = "select * from pastoralists where phone_number = %(phone)s"

UPSERT_SQL = """
insert into pastoralists (phone_number, preferred_language, primary_species)
values (%(phone)s, %(language)s, %(species)s)
on conflict (phone_number) do update set
    preferred_language = coalesce(excluded.preferred_language, pastoralists.preferred_language),
    primary_species     = coalesce(excluded.primary_species, pastoralists.primary_species)
returning *
"""

SET_VOICE_REPLIES_SQL = """
update pastoralists
set voice_replies = %(enabled)s
where phone_number = %(phone)s
"""

UPDATE_LOCATION_SQL = """
update pastoralists
set last_known_location = st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326)
where phone_number = %(phone)s
"""

SET_WATER_SOURCE_SQL = """
update pastoralists
set water_source_id = %(water_source_id)s
where phone_number = %(phone)s
"""


@dataclass
class Pastoralist:
    id: str
    phone_number: str
    preferred_language: str
    primary_species: str | None
    voice_replies: bool = False
    full_name: str | None = None
    herd_composition: dict | None = None
    onboarded_at: object | None = None
    water_source_id: str | None = None

    @property
    def is_onboarded(self) -> bool:
        return self.onboarded_at is not None

    @property
    def first_name(self) -> str | None:
        if not self.full_name:
            return None
        return self.full_name.split()[0]


def get_pastoralist(phone_number: str) -> Pastoralist | None:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(GET_SQL, {"phone": phone_number})
            row = cur.fetchone()
    if not row:
        return None
    return _from_row(row)


def upsert_pastoralist(phone_number: str, language: str | None = None, species: str | None = None) -> Pastoralist:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(UPSERT_SQL, {"phone": phone_number, "language": language or "swahili", "species": species})
            row = cur.fetchone()
        conn.commit()
    return _from_row(row)


def _from_row(row) -> Pastoralist:
    import json

    herd = row.get("herd_composition")
    if isinstance(herd, str):
        try:
            herd = json.loads(herd)
        except Exception:  # noqa: BLE001
            herd = {}
    return Pastoralist(
        id=str(row["id"]),
        phone_number=row["phone_number"],
        preferred_language=row["preferred_language"],
        primary_species=row["primary_species"],
        voice_replies=bool(row.get("voice_replies", False)),
        full_name=row.get("full_name"),
        herd_composition=herd,
        onboarded_at=row.get("onboarded_at"),
        water_source_id=str(row["water_source_id"]) if row.get("water_source_id") else None,
    )


def set_voice_replies(phone_number: str, enabled: bool) -> None:
    """Persist the herder's voice-reply preference (said 'sauti'/'voice')."""
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SET_VOICE_REPLIES_SQL, {"phone": phone_number, "enabled": enabled})
        conn.commit()


def update_last_location(phone_number: str, lon: float, lat: float) -> None:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(UPDATE_LOCATION_SQL, {"phone": phone_number, "lon": lon, "lat": lat})
        conn.commit()


def set_water_source(phone_number: str, water_source_id: str) -> None:
    """Remember the water point the herder confirmed their animals drink from."""
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SET_WATER_SOURCE_SQL,
                        {"phone": phone_number, "water_source_id": water_source_id})
        conn.commit()


def get_water_source(phone_number: str) -> dict | None:
    """Return the herder's confirmed water point: {id, name, ward, county, lon, lat}."""
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """select ws.id, ws.name, ws.ward, ws.county, st_x(ws.geom) as lon, st_y(ws.geom) as lat
                   from pastoralists p join water_sources ws on ws.id = p.water_source_id
                   where p.phone_number = %(phone)s""",
                {"phone": phone_number},
            )
            row = cur.fetchone()
    if not row:
        return None
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "ward": row["ward"],
        "county": row["county"],
        "lon": float(row["lon"]),
        "lat": float(row["lat"]),
    }


def get_last_location(phone_number: str) -> tuple[float, float] | None:
    """Return (lon, lat) of the herder's last shared location, or None."""
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """select st_x(last_known_location) as lon, st_y(last_known_location) as lat
                   from pastoralists where phone_number = %(phone)s""",
                {"phone": phone_number},
            )
            row = cur.fetchone()
    if not row or row["lon"] is None:
        return None
    return float(row["lon"]), float(row["lat"])


def delete_pastoralist(phone_number: str) -> None:
    """Remove the herder's profile + ground-truth reports (data-deletion request)."""
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from pastoralists where phone_number = %(phone)s",
                        {"phone": phone_number})
        conn.commit()
