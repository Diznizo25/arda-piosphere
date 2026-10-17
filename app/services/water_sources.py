"""Water-source registration + zone creation, shared by the HTTP API
(app/routers/water_sources.py) and the CLI pin flow (scripts/pin_water_point.py).

Uses raw PostGIS SQL (ST_MakePoint / ST_Buffer on geography casts) — the
Supabase REST client can't express geometry creation, and this must run as one
transaction so a half-created point never lingers.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import get_species_rings
from app.db import get_pg_connection

log = logging.getLogger(__name__)

INSERT_WATER_SOURCE_SQL = """
insert into water_sources (geom, source_type, source_ref, name, water_type, ward, county, confidence, last_confirmed)
values (
    st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326),
    %(source_type)s,
    %(source_ref)s,
    %(name)s,
    %(water_type)s,
    %(ward)s,
    %(county)s,
    %(confidence)s,
    now()
)
returning id, st_x(geom) as lon, st_y(geom) as lat, source_type, source_ref, name, water_type, ward, county, confidence, last_confirmed
"""

INSERT_ZONE_SQL = """
insert into piosphere_zones (water_source_id, species, radius_km, geom, last_computed)
values (%(water_source_id)s, %(species)s, %(radius_km)s,
        st_buffer(st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326)::geography,
                  %(radius_m)s)::geometry(Polygon, 4326),
        null)
"""

LIST_WATER_SOURCES_SQL = """
select ws.id, st_x(ws.geom) as lon, st_y(ws.geom) as lat,
       ws.source_type, ws.source_ref, ws.name, ws.water_type, ws.ward, ws.county,
       ws.confidence, ws.last_confirmed, ws.status,
       (select count(*) from piosphere_zones pz where pz.water_source_id = ws.id) as zone_count
from water_sources ws
order by ws.created_at desc
"""

GET_WATER_SOURCE_SQL = """
select ws.id, st_x(ws.geom) as lon, st_y(ws.geom) as lat,
       ws.source_type, ws.source_ref, ws.name, ws.water_type, ws.ward, ws.county,
       ws.confidence, ws.last_confirmed, ws.status,
       (select count(*) from piosphere_zones pz where pz.water_source_id = ws.id) as zone_count
from water_sources ws
where ws.id = %(id)s
"""

# Only these columns may ever be edited from a conversation. Coordinates are NOT
# here on purpose: moving a point also means moving its species rings and its
# precomputed rasters, so a "move" is a new point (the pin flow), never an edit.
_EDITABLE_COLUMNS = ("name", "water_type", "ward")

ZONES_FOR_WATER_SOURCE_SQL = """
select species, radius_km, st_asgeojson(geom) as geojson
from piosphere_zones
where water_source_id = %(water_source_id)s
order by radius_km asc
"""


@dataclass
class WaterSource:
    id: str
    lon: float
    lat: float
    source_type: str
    source_ref: str | None
    name: str | None = None
    water_type: str | None = None
    ward: str | None = None
    county: str = "Isiolo"
    confidence: float = 0.5
    last_confirmed: object | None = None
    zone_count: int = 0
    # Last reported state ('unknown' | 'flowing' | 'functional' | ...). Read-only
    # here: status is written by the herder-report loop, never by an edit flow.
    status: str | None = None

    @property
    def label(self) -> str:
        """A herder-recognisable label: local name first, ward as fallback."""
        return self.name or self.ward or "Maji"


def create_water_source(
    lon: float,
    lat: float,
    source_type: str = "ground_truth",
    source_ref: str | None = None,
    name: str | None = None,
    water_type: str | None = None,
    ward: str | None = None,
    county: str = "Isiolo",
    confidence: float = 0.5,
) -> WaterSource:
    """Insert a water point and its three species rings (cattle/shoat/camel) in
    one transaction. Ring radii come from config/species_rings.yaml."""
    rings = get_species_rings()
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                INSERT_WATER_SOURCE_SQL,
                {
                    "lon": lon, "lat": lat, "source_type": source_type,
                    "source_ref": source_ref, "name": name, "water_type": water_type,
                    "ward": ward, "county": county, "confidence": confidence,
                },
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("insert into water_sources returned no row")
            ws = WaterSource(
                id=str(row["id"]),
                lon=float(row["lon"]),
                lat=float(row["lat"]),
                source_type=row["source_type"],
                source_ref=row["source_ref"],
                name=row["name"],
                ward=row["ward"],
                county=row["county"],
                confidence=float(row["confidence"]),
                last_confirmed=row["last_confirmed"],
            )
            for species in rings.species_ordered_by_radius():
                radius_km = rings.radius_for(species)
                cur.execute(
                    INSERT_ZONE_SQL,
                    {
                        "water_source_id": ws.id,
                        "species": species,
                        "radius_km": radius_km,
                        "lon": lon, "lat": lat,
                        "radius_m": radius_km * 1000,
                    },
                )
        conn.commit()
    log.info(f"Created water source {ws.id} at ({lon:.5f}, {lat:.5f}) with 3 species rings")
    return ws


def set_ward(water_source_id: str, ward: str) -> None:
    """File a water point under the ward the herder (or our polygons) named."""
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update water_sources set ward = %(ward)s, updated_at = now() "
                "where id = %(id)s",
                {"ward": ward, "id": water_source_id},
            )
        conn.commit()
    log.info("water source %s filed under ward %r", water_source_id, ward)


def list_water_sources() -> list[WaterSource]:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(LIST_WATER_SOURCES_SQL)
            rows = cur.fetchall()
    return [_from_row(r) for r in rows]


def get_water_source(water_source_id: str) -> WaterSource | None:
    """One water point by id (the point a herder is calling "mine" right now).

    A single-row lookup on purpose: the change-water-point flow must not read the
    whole table to label one point, and the profile only stores the id.
    """
    if not water_source_id:
        return None
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(GET_WATER_SOURCE_SQL, {"id": water_source_id})
            row = cur.fetchone()
    return _from_row(row) if row else None


def update_water_source(water_source_id: str, *,
                        name: str | None = None,
                        water_type: str | None = None,
                        ward: str | None = None) -> WaterSource | None:
    """Correct the NAME, TYPE or WARD of an existing point (herder-requested).

    Why an update and not a new point: a herder who told us "Kisima cha Oldonyiro"
    and meant "Kisima cha Kipsing", or who waters at a tap we filed as a borehole,
    would otherwise have to create a duplicate -- and duplicates split the status
    reports and the rasters that make the map trustworthy.

    Coordinates are deliberately NOT editable here (see _EDITABLE_COLUMNS): moving
    a point is a re-pin, because the rings and the precomputed COGs must move too.

    Returns the updated row, or None when there is nothing to change.
    """
    values = {"id": water_source_id}
    sets: list[str] = []
    for column in _EDITABLE_COLUMNS:
        value = {"name": name, "water_type": water_type, "ward": ward}[column]
        if value is None:
            continue
        sets.append(f"{column} = %({column})s")
        values[column] = value
    if not sets:
        return None

    sql = (f"update water_sources set {', '.join(sets)}, updated_at = now() "
           "where id = %(id)s returning id")
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, values)
            row = cur.fetchone()
        conn.commit()
    if not row:
        log.warning("update_water_source: no point with id %s", water_source_id)
        return None
    log.info("water source %s updated (%s)", water_source_id, ", ".join(sets))
    return get_water_source(water_source_id)


def _from_row(r) -> WaterSource:
    """One place that maps a DB row to the dataclass, so list/get can never drift."""
    return WaterSource(
        id=str(r["id"]),
        lon=float(r["lon"]),
        lat=float(r["lat"]),
        source_type=r["source_type"],
        source_ref=r["source_ref"],
        name=r["name"],
        water_type=r["water_type"],
        ward=r["ward"],
        county=r["county"],
        confidence=float(r["confidence"]),
        last_confirmed=r["last_confirmed"],
        zone_count=int(r["zone_count"]),
        status=r.get("status") if hasattr(r, "get") else r["status"],
    )


def zones_for_water_source(water_source_id: str) -> list[dict]:
    """Return the species rings as [{species, radius_km, geojson}], ordered by
    radius — used by the map renderer."""
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(ZONES_FOR_WATER_SOURCE_SQL, {"water_source_id": water_source_id})
            rows = cur.fetchall()
    return [
        {"species": r["species"], "radius_km": float(r["radius_km"]), "geojson": r["geojson"]}
        for r in rows
    ]
