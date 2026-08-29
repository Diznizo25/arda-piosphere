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

UPDATE_LOCATION_SQL = """
update pastoralists
set last_known_location = st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326)
where phone_number = %(phone)s
"""


@dataclass
class Pastoralist:
    id: str
    phone_number: str
    preferred_language: str
    primary_species: str | None


def get_pastoralist(phone_number: str) -> Pastoralist | None:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(GET_SQL, {"phone": phone_number})
            row = cur.fetchone()
    if not row:
        return None
    return Pastoralist(
        id=str(row["id"]),
        phone_number=row["phone_number"],
        preferred_language=row["preferred_language"],
        primary_species=row["primary_species"],
    )


def upsert_pastoralist(phone_number: str, language: str | None = None, species: str | None = None) -> Pastoralist:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(UPSERT_SQL, {"phone": phone_number, "language": language or "borana", "species": species})
            row = cur.fetchone()
        conn.commit()
    return Pastoralist(
        id=str(row["id"]),
        phone_number=row["phone_number"],
        preferred_language=row["preferred_language"],
        primary_species=row["primary_species"],
    )


def update_last_location(phone_number: str, lon: float, lat: float) -> None:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(UPDATE_LOCATION_SQL, {"phone": phone_number, "lon": lon, "lat": lat})
        conn.commit()


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
