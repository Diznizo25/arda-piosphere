"""Full pasture-overlay render test with mocked data (no DB)."""
from __future__ import annotations

import sys
import types

import numpy as np

sys.path.insert(0, ".")
from app.services import map_renderer  # noqa: E402
from app.services import water_sources as ws_mod  # noqa: E402


def main() -> None:
    # 1) mercator forward with scalars + arrays
    assert abs(map_renderer._mercator_x(37.58) - 37.58 * 20037508.34 / 180.0) < 1
    arr_x = map_renderer._mercator_x(np.array([36.0, 37.0]))
    assert arr_x.shape == (2,), arr_x
    arr_y = map_renderer._mercator_y(np.array([0.3, 0.5]))
    assert arr_y.shape == (2,), arr_y
    # round-trip scalar
    assert abs(map_renderer._mercator_y_inv(map_renderer._mercator_y(0.35)) - 0.35) < 1e-6
    print("mercator scalar+array OK")

    # 2) mock the DB + COG and render the pasture map end-to-end
    fake_ws = types.SimpleNamespace(id="x", lon=37.5725, lat=0.3525, name="Isiolo town water",
                                    ward="Isiolo", county="Isiolo")
    import json as _json

    fake_zones = [
        {"species": "cattle", "radius_km": 7.0, "geojson": _json.dumps(_circle_geojson(37.5725, 0.3525, 0.06))},
        {"species": "shoat", "radius_km": 11.0, "geojson": _json.dumps(_circle_geojson(37.5725, 0.3525, 0.10))},
        {"species": "camel", "radius_km": 25.0, "geojson": _json.dumps(_circle_geojson(37.5725, 0.3525, 0.22))},
    ]
    ws_mod.list_water_sources = lambda: [fake_ws]
    ws_mod.zones_for_water_source = lambda wid: fake_zones

    # synthetic overview: (3, 64, 64) with realistic-ish values — the renderer
    # reads only the forage bands (NDVI, SATVI, BSI) via read_overview_array.
    h = w = 64
    ndvi = np.full((h, w), 0.12)
    ndvi[20:44, 20:44] = 0.45          # a "green" patch
    ndvi[10:20, 30:50] = 0.08          # a low patch
    satvi = np.full((h, w), 0.10)
    satvi[20:44, 20:44] = 0.13
    satvi[10:20, 30:50] = 0.30         # high SATVI low NDVI -> dry forage
    bsi = np.full((h, w), 0.20)
    bsi[20:44, 20:44] = 0.18
    bsi[10:20, 30:50] = 0.06
    bsi[30:40, 10:20] = 0.32           # high BSI -> bare
    arr = np.stack([ndvi, satvi, bsi])

    class FakeTransform:
        c = 37.40
        f = 0.45
        a = 0.005
        e = -0.005

    def fake_read(wsid, *args, **kwargs):
        return arr, FakeTransform()

    from app.services import raster_read
    raster_read.read_overview_array = fake_read

    png = map_renderer.render_rings_png("x", herder_lon=37.58, herder_lat=0.35,
                                        species="camel", pasture=True)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    print(f"full pasture render OK ({len(png)} bytes PNG)")

    # 3) confirm the overlay actually classified (green patch present) by
    #    checking the best-pasture output is not None
    img, best, note = map_renderer._build_pasture_overlay("x", 37.30 * 20037508.34 / 180.0,
                                                          map_renderer._mercator_y(0.55), 100.0)
    assert img is not None
    assert best is not None, "expected a best-pasture patch"
    print(f"pasture overlay OK best={best} note={note!r}")

    # 4) numbered confirmation markers + confirmed-water highlight render
    numbered = [
        {"water_source_id": "a", "lon": 37.58, "lat": 0.36, "ward": "Ward A"},
        {"water_source_id": "b", "lon": 37.62, "lat": 0.33, "ward": "Ward B"},
        {"water_source_id": "c", "lon": 37.55, "lat": 0.31, "ward": "Ward C"},
    ]
    png2 = map_renderer.render_rings_png(
        "x", herder_lon=37.58, herder_lat=0.35, species="camel", pasture=True,
        lang="swa", confirm_source_id="b", numbered_sources=numbered,
    )
    assert png2[:8] == b"\x89PNG\r\n\x1a\n"
    print(f"numbered + confirmed render OK ({len(png2)} bytes PNG)")

    # 4b) FIT view (options map): must zoom so herder + ALL numbered markers are
    #     on screen even when the markers are far away (no rings/pasture).
    far = [{"water_source_id": f"w{i}", "lon": 37.24, "lat": 0.57,
            "name": f"Far {i}", "water_type": "well", "ward": "W"} for i in range(1, 6)]
    far_pts = [dict(lon=p["lon"], lat=p["lat"]) for p in far] + [dict(lon=37.58, lat=0.35)]
    fx = np.asarray([p["lon"] for p in far_pts])
    fy = np.asarray([p["lat"] for p in far_pts])
    fmx = fx * 20037508.34 / 180.0
    fmy = 20037508.34 / 180.0 * (180.0 / np.pi
                                 * np.log(np.tan(np.pi / 4 + np.radians(fy) / 2)))
    png4 = map_renderer.render_rings_png(
        "x", herder_lon=37.58, herder_lat=0.35, species="camel", pasture=False,
        lang="swa", numbered_sources=far, fit_view=True)
    assert png4[:8] == b"\x89PNG\r\n\x1a\n"
    # replicate viewport and assert every marker is on screen
    span_m = max(float(fmx.max() - fmx.min()), float(fmy.max() - fmy.min()))
    mpp_t = max(span_m / (map_renderer.IMG_SIZE - 130), 5.0)
    lat_ref = float(np.mean(fy))
    zoom = max(6, min(14, int(np.floor(np.log2(
        156543.03392 * np.cos(np.radians(lat_ref)) / mpp_t)))))
    mpp = 156543.03392 * np.cos(np.radians(lat_ref)) / (2 ** zoom)
    west = float((fmx.min() + fmx.max()) / 2) - map_renderer.IMG_SIZE / 2 * mpp
    north = float((fmy.min() + fmy.max()) / 2) + map_renderer.IMG_SIZE / 2 * mpp
    for p in far_pts:
        x, y = map_renderer._lonlat_to_px(p["lon"], p["lat"], west, north, mpp)
        assert 25 <= x <= map_renderer.IMG_SIZE - 25, f"far marker off-screen x={x}"
        assert 25 <= y <= map_renderer.IMG_SIZE - 25, f"far marker off-screen y={y}"
    print(f"fit overview render OK ({len(png4)} bytes PNG, all markers on-screen)")

    # 5) no-COG fallback: when read_overview_array returns None the map must
    #    still render (never blank) with a "being prepared" notice
    raster_read.read_overview_array = lambda wid, *a, **k: None
    png3 = map_renderer.render_rings_png(
        "x", herder_lon=37.58, herder_lat=0.35, species="camel", pasture=True, lang="swa")
    assert png3[:8] == b"\x89PNG\r\n\x1a\n"
    print(f"no-COG fallback render OK ({len(png3)} bytes PNG)")


def _circle_geojson(lon, lat, radius_deg):
    import math
    coords = []
    for i in range(36):
        ang = 2 * math.pi * i / 36
        coords.append([lon + radius_deg * math.cos(ang), lat + radius_deg * math.sin(ang)])
    return {"type": "Polygon", "coordinates": [coords + [coords[0]]]}


if __name__ == "__main__":
    main()
