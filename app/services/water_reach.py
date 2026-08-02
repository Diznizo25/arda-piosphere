"""
Species-scoped nearest-water lookup. Pure PostGIS query — no GEE, no raster
reads here (architecture principle #1: this runs live, per WhatsApp message).

A water point only counts as "reachable" for a species if the herder's
current location falls inside THAT species' piosphere ring for that water
point (a camel herder 40km out can reach water a cattle herder at the same
spot cannot). Among reachable candidates, nearest wins.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.db import get_pg_connection

NEAREST_REACHABLE_SQL = """
select
    ws.id as water_source_id,
    ws.source_type,
    ws.confidence,
    ws.last_confirmed,
    st_x(ws.geom) as lon,
    st_y(ws.geom) as lat,
    st_asgeojson(pz.geom) as species_zone_geojson,
    st_distance(ws.geom::geography, st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326)::geography) as distance_m
from piosphere_zones pz
join water_sources ws on ws.id = pz.water_source_id
where pz.species = %(species)s
  and st_contains(pz.geom, st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326))
order by distance_m asc
limit %(limit)s
"""


@dataclass
class ReachableWater:
    water_source_id: str
    source_type: str
    confidence: float
    last_confirmed: datetime | None
    lon: float
    lat: float
    distance_m: float
    species_zone_geojson: str


def find_nearest_reachable_water(lon: float, lat: float, species: str, limit: int = 3) -> list[ReachableWater]:
    """Returns up to `limit` reachable water points for this species, nearest
    first. Empty list means no known water point has this species' ring
    reaching the herder's location — a real (if hopefully rare) answer, not
    an error: it should be surfaced to the herder honestly, not hidden."""
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(NEAREST_REACHABLE_SQL, {"lon": lon, "lat": lat, "species": species, "limit": limit})
            rows = cur.fetchall()

    return [
        ReachableWater(
            water_source_id=str(r["water_source_id"]),
            source_type=r["source_type"],
            confidence=float(r["confidence"]),
            last_confirmed=r["last_confirmed"],
            lon=r["lon"],
            lat=r["lat"],
            distance_m=float(r["distance_m"]),
            species_zone_geojson=r["species_zone_geojson"],
        )
        for r in rows
    ]
