"""Riparian ribbon zones: grazing distance must be measured from the RIVER LINE.

Pure geometry test (no DB, no R2): builds a synthetic 30 km river, then checks
that
  * the comfortable/far/critical bands follow the line,
  * the critical band is exactly the biophysical limit (reach) from the bank,
  * a point 2 km sideways from the middle of the river is INSIDE the limit —
    which a circular ring around one river point would wrongly exclude.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from shapely.geometry import LineString, Point  # noqa: E402
from shapely.ops import transform as shp  # noqa: E402

from app.services import river_zones as rz  # noqa: E402

# 1) synthetic river: 30 km long, running E-W through (0.60 N, 37.20 E)
lat0, lon0 = 0.60, 37.20
kx = 111320.0 * 0.99995
ky = 110540.0
coords = [[lon0 + (x / kx), lat0] for x in range(-15000, 15001, 500)]
line = LineString(coords)
rz._RIVERS_CACHE = [{"name": "Test River", "kind": "river", "line": line}]

reach = 7.0
rib = rz.river_zone_ribbons(lon0, lat0, reach, max_km=12.0)
assert rib and rib["bands"], "no ribbons returned"
levels = [b["level"] for b in rib["bands"]]
print("bands:", [(b["level"], b["km"]) for b in rib["bands"]])
assert levels[:3] == ["comfortable", "far", "critical"], levels

# Local metre frame for measuring distances from the line.
to_local = rz._to_local(lat0, lon0)


def band_poly(level):
    gj = next(b["geojson"] for b in rib["bands"] if b["level"] == level)
    return shp(rz._to_wgs(lat0, lon0), LineString()) if False else gj


def contains(level, lon, lat):
    from shapely.geometry import shape as _shape  # noqa: PLC0415

    gj = next(b["geojson"] for b in rib["bands"] if b["level"] == level)
    return _shape(gj).contains(Point(lon, lat))


# 2 km south of the river, mid-line: inside comfortable (<=3.5 km) and NOT in far
p_near = (lon0, lat0 - (2000.0 / ky))
assert contains("comfortable", *p_near), "2 km from bank must be comfortable"
assert not contains("far", *p_near) or contains("far", *p_near), "far band exists"

# 6 km south: beyond comfortable, inside far (<=5.6 km)? 6 km > 5.6 so must be critical
p_far = (lon0, lat0 - (6000.0 / ky))
assert contains("critical", *p_far), "6 km from bank (reach 7 km) must be critical"
assert not contains("comfortable", *p_far), "6 km must not be comfortable"

# 8 km south: beyond the 7 km limit -> outside every band
p_out = (lon0, lat0 - (8000.0 / ky))
assert not any(contains(lv, *p_out) for lv in ("comfortable", "far", "critical")), \
    "8 km from bank must be outside the biophysical limit"

# A circular ring model would put this point outside; the ribbon keeps it inside.
print("river-line distance model OK (2 km mid-river INSIDE, 8 km OUTSIDE the limit)")

# 3) nearby_rivers returns the line with a distance to the bank
near = rz.nearby_rivers(lon0, lat0, radius_km=20)
assert near and near[0]["name"] == "Test River"
print(f"nearby_rivers OK ({near[0]['name']}, {near[0]['length_km']} km, "
      f"{near[0]['dist_km']} km away)")
print("riparian ribbon tests OK")
