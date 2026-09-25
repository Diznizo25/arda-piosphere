"""Piosphere rings must draw as clean circles, not as wobbly polygons.

Why this test exists: a herder looks at the map and sees a boundary that is "curved
and not neat" — the rings are supposed to be circles, so anything that makes them
wobbly is a bug, not a style choice. Two things made them wobbly:

  1. PostGIS ST_Buffer on geography defaults to 8 segments per quarter circle = 32
     straight segments (33 vertices). At Isiolo's radii that is up to ~113 m of flat
     edge on a 25 km ring.
  2. The renderer then ran shapely simplify(0.0004 degrees) — ~44 m of allowed error
     on a ring that was already coarse.

What is measured here is what the eye sees: the stored geometry is projected through
the renderer's OWN projection code at the zoom we actually render, and every drawn
vertex's distance from the projected centre is compared with the ideal radius. The
same ring is then decimated to 32 segments to reproduce the old behaviour, so the
improvement is a number rather than an impression.

DB parts are skipped (with a notice) when no database is reachable.
"""
import io
import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.services.map_renderer import (  # noqa: E402
    IMG_SIZE,
    _lonlat_to_px,
    _mercator_x,
    _mercator_y,
)

RADII_KM = {"cattle": 7.0, "shoat": 11.0, "camel": 25.0}


def drawn_radii_px(coords, lon0: float, lat0: float, radius_km: float) -> tuple[list[float], float]:
    """Radii (px) of the DRAWN LINE, sampled along every edge, plus the ideal radius.

    Sampling the vertices alone would miss the bug entirely: a 32-gon's vertices all
    sit exactly on the circle, and it is the straight edges *between* them that the
    eye reads as a polygon. So this walks the projected polyline, which is what PIL
    actually strokes.
    """
    mpp = 2 * radius_km * 1000 / IMG_SIZE
    cx, cy = _mercator_x(lon0), _mercator_y(lat0)
    west, north = cx - IMG_SIZE / 2 * mpp, cy + IMG_SIZE / 2 * mpp
    ideal = (radius_km * 1000) / mpp
    drawn = [_lonlat_to_px(px, py, west, north, mpp) for px, py in coords]
    radii = []
    for (x1, y1), (x2, y2) in zip(drawn, drawn[1:]):
        for t in (0.0, 0.25, 0.5, 0.75, 1.0):
            x, y = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
            radii.append(math.hypot(x - IMG_SIZE / 2, y - IMG_SIZE / 2))
    return radii, ideal


def facet_residual_px(coords, lon0: float, lat0: float, radius_km: float) -> tuple[float, float]:
    """(faceting residual, total wobble) in pixels.

    Two different errors hide in a ring's drawn radius, and only one of them is visible:

      * a slow, gentle variation around the ring — the ellipsoidal ground circle drawn
        on spherical Web Mercator is a ~0.7% ellipse. Nobody can see 0.7%; it is the
        "total wobble" number here, reported but not asserted.
      * high-frequency ripples: the straight chords between vertices (a 32-gon is ~2.4 px
        flat on a 7 km ring), plus whatever shapely simplify() ate. That is what makes a
        ring look polygonal — "curved and not neat". It is measured by smoothing the
        radius profile over a sixteenth of the ring and taking the residual.
    """
    radii, _ = drawn_radii_px(coords, lon0, lat0, radius_km)
    n = len(radii)
    window = max(3, n // 16)
    smooth = []
    for i in range(n):
        lo, hi = i - window // 2, i + window // 2
        vals = [radii[j % n] for j in range(lo, hi + 1)]
        smooth.append(sum(vals) / len(vals))
    residual = max(abs(r - s) for r, s in zip(radii, smooth))
    return residual, max(radii) - min(radii)


def synthetic_ring(radius_km: float, segments: int, lat0: float = 0.5669,
                   lon0: float = 37.2402) -> list[tuple[float, float]]:
    """A ground circle via the spherical direct formula (shape-accurate at 7-25 km)."""
    earth_r = 6371008.8
    delta = radius_km * 1000 / earth_r
    phi1, lam1 = math.radians(lat0), math.radians(lon0)
    pts = []
    for i in range(segments + 1):
        theta = 2 * math.pi * i / segments
        phi2 = math.asin(math.sin(phi1) * math.cos(delta)
                         + math.cos(phi1) * math.sin(delta) * math.cos(theta))
        lam2 = lam1 + math.atan2(math.sin(theta) * math.sin(delta) * math.cos(phi1),
                                 math.cos(delta) - math.sin(phi1) * math.sin(phi2))
        pts.append((math.degrees(lam2), math.degrees(phi2)))
    return pts


def fetch_ring(species: str):
    """One real stored ring: (coords, lon, lat) of its water point, or None."""
    try:
        from app.db import get_pg_connection

        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    select st_x(ws.geom) as lon, st_y(ws.geom) as lat,
                           st_asgeojson(pz.geom) as geom
                    from piosphere_zones pz
                    join water_sources ws on ws.id = pz.water_source_id
                    where pz.species = %(species)s
                    order by ws.created_at
                    limit 1
                """, {"species": species})
                row = cur.fetchone()
        if not row:
            return None
        coords = [(float(x), float(y)) for x, y in
                  json.loads(row["geom"])["coordinates"][0]]
        return coords, float(row["lon"]), float(row["lat"])
    except Exception as exc:  # noqa: BLE001
        print(f"  (database unavailable: {type(exc).__name__} — using a synthetic ring)")
        return None


# --- 1) every species ring must draw as a circle --------------------------
for species, radius_km in RADII_KM.items():
    got = fetch_ring(species)
    if got:
        coords, lon0, lat0 = got
        label = f"{species} ring (stored, {len(coords)} vertices)"
    else:
        coords, lon0, lat0 = synthetic_ring(radius_km, 512), 37.2402, 0.5669
        label = f"{species} ring (synthetic, {len(coords)} vertices)"

    residual, wobble = facet_residual_px(coords, lon0, lat0, radius_km)
    # Sub-pixel: the drawn line must be a smooth curve, not a chain of visible flats.
    assert residual < 0.75, (f"{label}: drawn boundary is faceted by {residual:.2f} px — "
                             f"it will read as a polygon, not a circle")
    print(f"{label}: faceting {residual:.2f} px, total wobble {wobble:.2f} px "
          f"(the rest is the ~0.7% ellipsoid/mercator ellipse, invisible)")

    # The old geometry: the same ring decimated to 32 segments — what the map looked
    # like before the segment count was raised.
    step = max(1, len(coords) // 32)
    old_residual, _ = facet_residual_px(coords[::step], lon0, lat0, radius_km)
    assert old_residual > 0.75, ("a 32-segment ring should show visible flat edges — "
                                 f"measured only {old_residual:.2f} px")
    assert old_residual > 5 * residual, ("the coarse ring must be clearly worse than "
                                         "the dense one — otherwise the fix is moot")
    print(f"  same ring decimated to 32 segments: faceting {old_residual:.2f} px "
          f"({old_residual / max(residual, 1e-6):.0f}x worse — the reported bug)")

# --- 2) the renderer must not simplify the rings or stack fills ------------
src = io.open("app/services/map_renderer.py", encoding="utf-8").read()
assert "simplify(tolerance=0.0004" not in src, "the lossy ring simplify is back"
assert "NO simplify() here" in src, "the reason the simplify was removed must stay"
assert "fill=style[0] if active else None" in src, \
    "only the herder's own ring may be filled (muddy stacked fills)"
assert "is too coarse to look" in src, "coarse rings must be reported, not drawn silently"
print("renderer: no lossy simplify, one filled ring, coarse rings reported")

# --- 3) the generator asks PostGIS for enough segments -------------------
gen = io.open("scripts/generate_piosphere_zones.py", encoding="utf-8").read()
assert "SEGMENTS_PER_QUARTER = 128" in gen, "the ring segment count was reduced"
assert "st_buffer(ws.geom::geography, %(radius_m)s, %(segments)s)" in gen, \
    "the buffer must pass the segment count through"
print("generator: 128 segments per quarter (512 vertices)")

# --- 4) every stored ring, not just the sampled one ----------------------
try:
    from app.db import get_pg_connection

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select ws.name, pz.species, pz.radius_km, st_npoints(pz.geom) as vertices,
                       round((st_perimeter(pz.geom::geography) / 1000.0)::numeric, 1) as perim,
                       round((2 * pi() * pz.radius_km)::numeric, 1) as ideal
                from piosphere_zones pz
                join water_sources ws on ws.id = pz.water_source_id
                order by ws.name, pz.radius_km
            """)
            rows = cur.fetchall()
    assert rows, "no piosphere_zones rows to check"
    coarse = [r for r in rows if r["vertices"] < 300]
    assert not coarse, (f"{len(coarse)} ring(s) too coarse to look circular: "
                        f"{[(r['name'], r['species'], r['vertices']) for r in coarse][:4]}"
                        f" — re-run scripts/generate_piosphere_zones.py")
    bad = [r for r in rows
           if abs(float(r["perim"]) - float(r["ideal"])) / float(r["ideal"]) > 0.005]
    assert not bad, (f"{len(bad)} ring(s) are not circles (perimeter off >0.5%): "
                     f"{[(r['name'], r['species'], r['perim'], r['ideal']) for r in bad][:4]}")
    print(f"stored rings: {len(rows)} checked, all >=300 vertices and circular to 0.5%")
except AssertionError:
    raise
except Exception as exc:  # noqa: BLE001
    print(f"stored-ring sweep skipped (no database here): {type(exc).__name__}")

print("\nRING SHAPE OK")
