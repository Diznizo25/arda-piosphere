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
       ws.confidence, ws.last_confirmed,
       (select count(*) from piosphere_zones pz where pz.water_source_id = ws.id) as zone_count
from water_sources ws
order by ws.created_at desc
"""

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


def list_water_sources() -> list[WaterSource]:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(LIST_WATER_SOURCES_SQL)
            rows = cur.fetchall()
    return [
        WaterSource(
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
        )
        for r in rows
    ]


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
