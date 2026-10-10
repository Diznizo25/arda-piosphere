"""Which ward is this? — answered from our own ward polygons, not from the herder.

A pastoralist should never have to know or spell his ward. He sends a location, and this
module says which ward it falls in, so the system can *confirm* it back to him
("Tumeandika: Burat") and store it. The polygons are the per-ward files in
`config/wards/` (produced by `scripts/split_wards.py` from the national ADM3 file).

Deliberately pure Python — the web service carries no GIS stack, and a ray-casting test
against a 10-vertex polygon is microseconds. Fail-open: if a polygon file is missing or
unreadable we return an unknown ward, and the pin flow asks the herder instead of
blocking him.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

WARD_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "wards"
# The national file is ~5 MB and every Isiolo ward is duplicated in its own small file:
# never load the national one into the web service.
SKIP_FILES = {"kenya_adm3_wards.geojson"}
COUNTY = "Isiolo"
# Isiolo town's wards: few livestock decisions, dense phones, and a water point that is a
# tap rather than a grazing centre (see the peri-urban note in the pin flow).
PERI_URBAN_WARDS = {"wabera", "bulla pesa"}

RURAL = "rural"
PERI_URBAN = "peri_urban"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class WardHit:
    """Where a coordinate falls, as far as we can tell."""

    ward: str | None
    county: str
    context: str  # rural | peri_urban | unknown

    @property
    def known(self) -> bool:
        return bool(self.ward)


def _rings(geometry: dict) -> list[list[tuple[float, float]]]:
    """Exterior rings of a Polygon or MultiPolygon, as (lon, lat) pairs."""
    gtype = (geometry or {}).get("type")
    coords = (geometry or {}).get("coordinates") or []
    rings: list[list[tuple[float, float]]] = []
    if gtype == "Polygon":
        if coords:
            rings.append([(float(p[0]), float(p[1])) for p in coords[0]])
    elif gtype == "MultiPolygon":
        for poly in coords:
            if poly:
                rings.append([(float(p[0]), float(p[1])) for p in poly[0]])
    return rings


@lru_cache(maxsize=1)
def _load() -> tuple[tuple[str, tuple[tuple[tuple[float, float], ...], ...]], ...]:
    """[(ward, (ring, ...)), ...] for every ward file we ship. Loaded once per process."""
    out: list[tuple[str, tuple[tuple[tuple[float, float], ...], ...]]] = []
    if not WARD_DIR.exists():
        log.warning("ward directory missing: %s", WARD_DIR)
        return tuple()
    for path in sorted(WARD_DIR.glob("*.geojson")):
        if path.name in SKIP_FILES:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            log.warning("could not read ward file %s", path.name, exc_info=True)
            continue
        features = (data.get("features") if data.get("type") == "FeatureCollection"
                    else [data])
        for feature in features or []:
            props = feature.get("properties") or {}
            ward = props.get("ward") or props.get("shapeName") or path.stem.title()
            rings = tuple(tuple(r) for r in _rings(feature.get("geometry") or {}))
            if rings:
                out.append((str(ward), rings))
    log.info("loaded %d ward polygon(s) from %s", len(out), WARD_DIR.name)
    return tuple(out)


def _inside(lon: float, lat: float, ring: tuple[tuple[float, float], ...]) -> bool:
    """Ray casting: is (lon, lat) inside this ring?"""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            x_cross = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
            if lon < x_cross:
                inside = not inside
    return inside


def context_for(ward: str | None) -> str:
    if not ward:
        return UNKNOWN
    return PERI_URBAN if ward.strip().lower() in PERI_URBAN_WARDS else RURAL


def ward_for(lat: float | None, lon: float | None) -> WardHit:
    """Which ward does this coordinate fall in? `unknown` if we cannot tell."""
    if lat is None or lon is None:
        return WardHit(None, COUNTY, UNKNOWN)
    try:
        for ward, rings in _load():
            for ring in rings:
                if _inside(float(lon), float(lat), ring):
                    return WardHit(ward, COUNTY, context_for(ward))
    except Exception:  # noqa: BLE001
        log.warning("ward lookup failed for (%.5f, %.5f)", lat, lon, exc_info=True)
    return WardHit(None, COUNTY, UNKNOWN)


def known_wards() -> list[str]:
    """Ward names we can recognise — used to accept a correction from the herder."""
    return sorted({ward for ward, _ in _load()})


def match_ward(text: str | None) -> str | None:
    """A herder-typed ward name, matched case-insensitively against what we ship."""
    cleaned = (text or "").strip().lower().replace("_", " ")
    if not cleaned:
        return None
    for ward in known_wards():
        if cleaned == ward.lower():
            return ward
    return None
