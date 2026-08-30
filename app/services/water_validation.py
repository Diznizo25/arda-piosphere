"""Water-point PIN validation.

Before a herder's shared location is registered as a new water source (and
before we spend GEE compute building it), check the location against what we
already know:

  - if it's within a few metres of an existing registered water point, it is a
    duplicate -> tell the herder it's already registered.
  - if it's near (but not on top of) a known water point, surface that so the
    herder can confirm the exact spot.
  - otherwise it is an unknown location; we still allow registration but only
    after the herder confirms what kind of water point it is (borehole, well,
    pan, river, spring) and that the pin is accurate.

All checks are pure PostGIS on precomputed data - no GEE, no raster reads
(architecture principle #1).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.db import get_pg_connection

# If the pin is within this distance of an existing water source, treat it as
# that same source (duplicate), not a new one.
DUPLICATE_DISTANCE_M = 300.0
# If within this distance, tell the herder a known water point is nearby.
NEARBY_DISTANCE_M = 2000.0

WATER_TYPES = {
    "borehole": {"swahili": "Kisima (borehole)", "english": "Borehole"},
    "well": {"swahili": "Kisima cha kuchimba (well)", "english": "Well"},
    "pan": {"swahili": "Kijito / bwawa (pan)", "english": "Pan / dam"},
    "river": {"swahili": "Mto (river)", "english": "River"},
    "spring": {"swahili": "Chemchemi (spring)", "english": "Spring"},
}

NEAREST_SOURCE_SQL = """
select ws.id, ws.source_type, ws.confidence,
       st_x(ws.geom) as lon, st_y(ws.geom) as lat,
       st_distance(ws.geom::geography,
                   st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326)::geography) as distance_m
from water_sources ws
order by ws.geom <-> st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326)
limit 1
"""


@dataclass
class PinValidation:
    is_duplicate: bool
    duplicate_water_source_id: str | None
    distance_to_nearest_m: float | None
    nearest_source_type: str | None
    nearest_source_id: str | None
    nearest_confidence: float | None

    @property
    def has_nearby_source(self) -> bool:
        return (
            not self.is_duplicate
            and self.distance_to_nearest_m is not None
            and self.distance_to_nearest_m <= NEARBY_DISTANCE_M
        )


def validate_pin(lon: float, lat: float) -> PinValidation:
    """Check a candidate pin location against registered water sources."""
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(NEAREST_SOURCE_SQL, {"lon": lon, "lat": lat})
            row = cur.fetchone()

    if row is None:
        return PinValidation(
            is_duplicate=False,
            duplicate_water_source_id=None,
            distance_to_nearest_m=None,
            nearest_source_type=None,
            nearest_source_id=None,
            nearest_confidence=None,
        )

    dist = float(row["distance_m"])
    return PinValidation(
        is_duplicate=dist <= DUPLICATE_DISTANCE_M,
        duplicate_water_source_id=str(row["id"]) if dist <= DUPLICATE_DISTANCE_M else None,
        distance_to_nearest_m=dist,
        nearest_source_type=row["source_type"],
        nearest_source_id=str(row["id"]),
        nearest_confidence=float(row["confidence"]) if row["confidence"] is not None else None,
    )


def water_type_label(language: str = "swahili") -> dict[str, str]:
    """Map water-type key -> localized label for interactive buttons."""
    return {k: v[language] for k, v in WATER_TYPES.items()}
