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

# Guidance gate: a point reported DRY / BROKEN / NOT-FOUND recently must not be
# recommended (the herder would waste a day's trek). After STALE_STATUS_DAYS the
# point is allowed back — a dry pan refills, a pump gets repaired. Keep the day
# count in sync with app/services/water_status.STALE_STATUS_DAYS.
_UNUSABLE_FILTER = """
  and not (ws.status in ('dry','broken','not_found')
           and ws.status_updated_at > now() - interval '%(stale_days)s days')
"""

NEAREST_REACHABLE_SQL = """
select
    ws.id as water_source_id,
    ws.source_type,
    ws.confidence,
    ws.last_confirmed,
    ws.name,
    ws.water_type,
    ws.status,
    ws.status_updated_at,
    ws.status_reports,
    st_x(ws.geom) as lon,
    st_y(ws.geom) as lat,
    st_asgeojson(pz.geom) as species_zone_geojson,
    st_distance(ws.geom::geography, st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326)::geography) as distance_m
from piosphere_zones pz
join water_sources ws on ws.id = pz.water_source_id
where pz.species = %(species)s
  and st_contains(pz.geom, st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326))
""" + _UNUSABLE_FILTER + """
order by distance_m asc
limit %(limit)s
"""

# Same shape as above, but reach is a straight-line distance to the water point
# of `radius_m` (used when the herder's watering interval widens their effective
# reach beyond the stored ring — no recompute, geometry unchanged).
NEAREST_REACHABLE_DIST_SQL = """
select
    ws.id as water_source_id,
    ws.source_type,
    ws.confidence,
    ws.last_confirmed,
    ws.name,
    ws.water_type,
    ws.status,
    ws.status_updated_at,
    ws.status_reports,
    st_x(ws.geom) as lon,
    st_y(ws.geom) as lat,
    st_asgeojson(pz.geom) as species_zone_geojson,
    st_distance(ws.geom::geography, st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326)::geography) as distance_m
from piosphere_zones pz
join water_sources ws on ws.id = pz.water_source_id
where pz.species = %(species)s
  and st_dwithin(ws.geom::geography,
                 st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326)::geography,
                 %(radius_m)s)
""" + _UNUSABLE_FILTER + """
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
    name: str | None = None
    water_type: str | None = None
    status: str | None = None
    status_updated_at: datetime | None = None
    status_reports: int = 0
    needs_check: bool = True


def _stale_days() -> int:
    from app.services import water_status

    return int(water_status.STALE_STATUS_DAYS)


def find_nearest_reachable_water(lon: float, lat: float, species: str, limit: int = 3,
                                 effective_radius_km: float | None = None) -> list[ReachableWater]:
    """Returns up to `limit` reachable water points for this species, nearest
    first — EXCLUDING points recently reported dry/broken/no-longer-there, so we
    never send a herd to dead water. Empty list means no known water point has
    this species' ring reaching the herder's location — a real (if hopefully
    rare) answer, not an error: it should be surfaced honestly, not hidden.

    `effective_radius_km` widens reach beyond the stored ring for herders who
    water every 2-3 days (longer watering interval => they can graze further
    from water); the stored ring geometry is still returned for pasture stats.
    """
    params = {"lon": lon, "lat": lat, "species": species, "limit": limit,
              "stale_days": _stale_days()}
    if effective_radius_km is not None:
        sql = NEAREST_REACHABLE_DIST_SQL
        params["radius_m"] = int(effective_radius_km * 1000)
    else:
        sql = NEAREST_REACHABLE_SQL
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    from app.services import water_status

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
            name=r.get("name"),
            water_type=r.get("water_type"),
            status=r.get("status"),
            status_updated_at=r.get("status_updated_at"),
            status_reports=int(r.get("status_reports") or 0),
            needs_check=water_status.needs_check(r.get("status"),
                                                 r.get("status_updated_at"),
                                                 r["last_confirmed"]),
        )
        for r in rows
    ]


NEARBY_SQL = """
select
    ws.id as water_source_id,
    ws.name,
    ws.water_type,
    ws.ward,
    ws.county,
    ws.source_type,
    ws.source_ref,
    ws.status,
    ws.status_updated_at,
    ws.status_reports,
    ws.last_confirmed,
    st_x(ws.geom) as lon,
    st_y(ws.geom) as lat,
    st_distance(ws.geom::geography, st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326)::geography) as distance_m
from water_sources ws
order by distance_m asc
limit %(limit)s
"""

_PINNED_TYPE = {"borehole": "kisima (borehole)", "well": "kisima cha kuchimba",
                "river": "mto", "spring": "chemchem", "dam": "bwawa", "tap": "mfereji"}
_SOURCE_TYPE_SWA = {"satellite_gsw": "maji (GSW)", "osm": "maji (OSM)",
                    "wpdx": "maji (WPDx)", "ilri": "maji (ILRI)",
                    "ground_truth": "maji"}


def list_nearby_water_sources(lon: float, lat: float, limit: int = 10) -> list[dict]:
    """Nearest water points to a location (any species), for the herder's
    "which water point do your animals drink from?" confirmation list.

    Returns [{water_source_id, name, ward, county, source_type, source_ref,
              water_type, lon, lat, distance_km, direction_swa, status,
              status_label, status_age_days, needs_check}] — the exact named
    options we present to the herder (name first, ward as fallback), with the
    herder-reported status so dead water is visible in the list too.
    """
    from app.services import water_status

    from app.services.map_renderer import _bearing_deg, _compass_swa

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(NEARBY_SQL, {"lon": lon, "lat": lat, "limit": limit})
            rows = cur.fetchall()
    out = []
    for r in rows:
        ref = r["source_ref"] or ""
        water_type = r["water_type"]
        if not water_type and ref.startswith("whatsapp:"):
            parts = ref.split(":")
            water_type = parts[1] if len(parts) > 1 else None
        bearing = _bearing_deg(lat, lon, float(r["lat"]), float(r["lon"]))
        status = r.get("status")
        out.append({
            "water_source_id": str(r["water_source_id"]),
            "name": r["name"],
            "water_type": water_type,
            "ward": r["ward"],
            "county": r["county"],
            "source_type": r["source_type"],
            "source_ref": ref,
            "lon": float(r["lon"]),
            "lat": float(r["lat"]),
            "distance_km": round(float(r["distance_m"]) / 1000.0, 1),
            "direction_swa": _compass_swa(bearing),
            "status": status,
            "status_label": water_status.status_label(status, "swa"),
            "status_age_days": (round(a) if (a := water_status.age_days(
                r.get("status_updated_at"))) is not None else None),
            "needs_check": water_status.needs_check(status, r.get("status_updated_at"),
                                                    r.get("last_confirmed")),
        })
    return out
