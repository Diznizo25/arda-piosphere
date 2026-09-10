"""Riparian grazing zones — distance ribbons along a river LINE.

Why not a ring: `water_sources.geom` is a Point and `piosphere_zones` are
circles (st_buffer of a point). That is right for a well or borehole, but wrong
for a river: a river is a LONG water body, so every metre of bank has water and
the distance an animal can walk is measured FROM THE LINE. The grazing zones of
a river are therefore ribbons that follow the river's shape.

Bands (fractions of the species' effective reach R from config/species_rings):
  comfortable : 0.0 – 0.5 R   easy daily grazing
  far         : 0.5 – 0.8 R   edge of the usual zone
  critical    : 0.8 – 1.0 R   the biophysical limit (beyond it the herd cannot
                              water daily)

Geometry is computed in a local equirectangular metric frame (metres) centred
on the water point and converted back to lon/lat — ~1% accurate at these
scales, with no projection library needed.
"""
from __future__ import annotations

import json
import math
import os

from shapely.geometry import LineString, Point, mapping
from shapely.ops import transform as shp_transform
from shapely.ops import unary_union

RIVERS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "rivers.geojson")
_RIVERS_CACHE: list[dict] | None = None

# Keep the payload light for phones: simplify ribbons/outlines to ~60 m.
SIMPLIFY_M = 60.0


def load_rivers() -> list[dict]:
    """[{name, kind, line}] from config/rivers.geojson; [] when unavailable."""
    global _RIVERS_CACHE
    if _RIVERS_CACHE is not None:
        return _RIVERS_CACHE
    out: list[dict] = []
    try:
        with open(RIVERS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        for ft in data.get("features", []):
            props = ft.get("properties") or {}
            geom = ft.get("geometry") or {}
            if geom.get("type") != "LineString":
                continue
            coords = geom.get("coordinates") or []
            if len(coords) < 2:
                continue
            out.append({"name": str(props.get("name") or "Mto")[:60],
                        "kind": str(props.get("kind") or "river"),
                        "line": LineString([(float(x), float(y)) for x, y in coords])})
    except Exception:  # noqa: BLE001
        out = []
    _RIVERS_CACHE = out
    return out


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _to_local(lat0: float, lon0: float):
    """lon/lat -> local metres (equirectangular about lat0/lon0)."""
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 110540.0

    def f(x, y, z=None):
        return ((x - lon0) * kx, (y - lat0) * ky)

    return f


def _to_wgs(lat0: float, lon0: float):
    """local metres -> lon/lat (inverse of _to_local)."""
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 110540.0

    def f(x, y, z=None):
        return (lon0 + x / kx, lat0 + y / ky)

    return f


def _geojson(geom, lat0: float, lon0: float) -> dict | None:
    """Local-metre geometry -> simplified WGS84 GeoJSON (None when empty)."""
    if geom is None or geom.is_empty:
        return None
    g = geom.simplify(SIMPLIFY_M, preserve_topology=True)
    if g.is_empty:
        return None
    wgs = shp_transform(_to_wgs(lat0, lon0), g)
    if wgs.is_empty:
        return None
    return json.loads(json.dumps(mapping(wgs)))


def nearby_rivers(lon: float, lat: float, radius_km: float,
                  limit: int = 8) -> list[dict]:
    """River/stream lines within `radius_km` of a point, with the distance from
    the point to the closest bank. Longest-first, one per distinct name."""
    rivers = load_rivers()
    if not rivers:
        return []
    to_local = _to_local(lat, lon)
    to_wgs = _to_wgs(lat, lon)
    clip_circle = Point(0.0, 0.0).buffer(radius_km * 1000.0, quad_segs=64)
    origin = Point(0.0, 0.0)
    out: list[dict] = []
    seen: set[str] = set()
    for rv in rivers:
        key = rv["name"].strip().lower()
        if key in seen:
            continue
        local = shp_transform(to_local, rv["line"])
        d_km = local.distance(origin) / 1000.0
        if d_km > radius_km:
            continue
        clipped = local.intersection(clip_circle)
        if clipped.is_empty:
            continue
        coords = [[round(x, 5), round(y, 5)] for x, y in
                  shp_transform(to_wgs, clipped).coords]
        if len(coords) < 2:
            continue
        seen.add(key)
        out.append({"name": rv["name"], "kind": rv["kind"],
                    "dist_km": round(d_km, 1),
                    "length_km": round(local.length / 1000.0, 1),
                    "geojson": {"type": "LineString", "coordinates": coords}})
    out.sort(key=lambda r: (-r["length_km"], r["dist_km"]))
    return out[:limit]



def river_zone_ribbons(lon: float, lat: float, reach_km: float,
                       max_km: float | None = None) -> dict | None:
    """Grazing-zone ribbons that follow the river line.

    Returns {"reach_km": R, "bands": [{level, km, geojson}, ...]}: each geojson
    is the polygon of that distance band measured from the WHOLE river line
    (comfortable / far / critical), clipped to `max_km` around the point.
    None when no river is close enough to matter.
    """
    if reach_km <= 0:
        return None
    max_km = max_km or reach_km * 1.15
    rivers = load_rivers()
    if not rivers:
        return None
    to_local = _to_local(lat, lon)
    origin = Point(0.0, 0.0)
    clip_circle = origin.buffer(max_km * 1000.0, quad_segs=64)
    lines = []
    for rv in rivers:
        local = shp_transform(to_local, rv["line"])
        if local.distance(origin) / 1000.0 > max_km:
            continue
        lines.append(local)
    if not lines:
        return None
    line_union = unary_union(lines)
    if line_union.is_empty:
        return None

    r_m = reach_km * 1000.0
    b_close = line_union.buffer(0.5 * r_m, quad_segs=32).intersection(clip_circle)
    b_far = line_union.buffer(0.8 * r_m, quad_segs=32).intersection(clip_circle)
    b_limit = line_union.buffer(r_m, quad_segs=32).intersection(clip_circle)

    bands = []
    for level, km, geom in (
        ("comfortable", 0.5 * reach_km, b_close),
        ("far", 0.8 * reach_km, b_far.difference(b_close)),
        ("critical", reach_km, b_limit.difference(b_far)),
    ):
        gj = _geojson(geom, lat, lon)
        if gj is not None:
            bands.append({"level": level, "km": round(km, 1), "geojson": gj})
    if not bands:
        return None
    return {"reach_km": round(reach_km, 1), "bands": bands}


__all__ = ["load_rivers", "nearby_rivers", "river_zone_ribbons", "_haversine_km"]

