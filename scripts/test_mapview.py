"""Interactive mapview + fit-overview smoke tests (real DB, sends mocked).

Covers:
  * the public /mapview page renders for swa+eng with water options embedded
  * the renderer's fit_view ("options map") puts herder + every numbered
    marker on screen even when they are 45-75 km apart
"""
from __future__ import annotations

import io
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, ".")

from app.routers.mapview import mapview_page  # noqa: E402
from app.services import map_renderer as m  # noqa: E402
from app.services import water_reach  # noqa: E402


def test_mapview_pages() -> None:
    # COG reads are slow from dev machines — patch the status (the live page
    # calls it for real on Render). Tests the page wiring only.
    real_status = m.pasture_overlay_status
    m.pasture_overlay_status = lambda *a, **k: {
        "bounds": [[0.50, 37.20], [0.62, 37.29]], "available": True,
        "usable_pct": 62, "frac": {"green": 20, "dry": 42, "bare": 12, "unclear": 26},
        "blank_pct": 18,
    }
    try:
        for lang in ("swa", "eng"):
            html = mapview_page(lat=0.3525, lon=37.583, species="camel", lang=lang,
                                id="88bc1e17-a5f6-4c4d-a410-e551e4af7e54",
                                numbered=None, name=None, interval="daily", focus=1)
            body = html.body.decode("utf-8", errors="replace")
            assert "leaflet" in body.lower()
            assert "__DATA__" not in body
            assert '"herder"' in body and '"options"' in body
            assert "L.imageOverlay" in body and "pasture.png" in body, "pasture overlay"
            assert "usable_pct" in body
            assert '"blank_pct": 18' in body, \
                "the blank share must reach the page (the router allow-list must pass it)"
            assert '"landmarks"' in body, "named places embedded"
            assert "esriTiles.addTo(map)" in body, "satellite base map loads first"
            assert "no_read" in body, "legend explains why a pixel is blank"
            print(f"{lang} mapview page OK ({len(body)} chars, pasture overlay present)")
        html2 = mapview_page(lat=0.3525, lon=37.583, species="camel", lang="swa",
                             id=None, name=None, interval="daily", focus=0,
                             numbered="88bc1e17-a5f6-4c4d-a410-e551e4af7e54")
        assert '"num"' in html2.body.decode("utf-8", errors="replace")
        print("numbered mapview page OK")
    finally:
        m.pasture_overlay_status = real_status


def test_fit_view_real() -> None:
    m._fetch_tile = lambda z, x, y: Image.new("RGB", (256, 256), (242, 239, 233))
    lat, lon = 0.3525, 37.583
    nearby = water_reach.list_nearby_water_sources(lon, lat, limit=10)
    assert nearby, "need sources"
    numbered = [{"water_source_id": n["water_source_id"], "lon": n["lon"],
                 "lat": n["lat"], "name": n["name"], "water_type": n["water_type"],
                 "ward": n["ward"]} for n in nearby]
    png = m.render_rings_png(nearby[0]["water_source_id"], herder_lon=lon,
                             herder_lat=lat, species="camel", pasture=False,
                             lang="swa", numbered_sources=numbered, fit_view=True)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pts = numbered + [{"lon": lon, "lat": lat}]
    fxs = np.asarray([p["lon"] for p in pts])
    fys = np.asarray([p["lat"] for p in pts])
    fmx = fxs * 20037508.34 / 180.0
    fmy = 20037508.34 / 180.0 * (180.0 / np.pi
                                 * np.log(np.tan(np.pi / 4 + np.radians(fys) / 2)))
    span_m = max(float(fmx.max() - fmx.min()), float(fmy.max() - fmy.min()))
    mpp_t = max(span_m / (m.IMG_SIZE - 130), 5.0)
    zoom = max(6, min(14, int(np.floor(np.log2(
        156543.03392 * np.cos(np.radians(float(np.mean(fys)))) / mpp_t)))))
    mpp = 156543.03392 * np.cos(np.radians(float(np.mean(fys)))) / (2 ** zoom)
    west = float((fmx.min() + fmx.max()) / 2) - m.IMG_SIZE / 2 * mpp
    north = float((fmy.min() + fmy.max()) / 2) + m.IMG_SIZE / 2 * mpp
    for p in pts:
        x, y = m._lonlat_to_px(p["lon"], p["lat"], west, north, mpp)
        assert 10 <= x <= m.IMG_SIZE - 10, f"marker off-screen x={x}"
        assert 10 <= y <= m.IMG_SIZE - 10, f"marker off-screen y={y}"
    print(f"fit-view real-DB OK (zoom {zoom}, {len(png)} bytes, all markers on-screen)")


def test_autofocus_shows_rings_and_legend() -> None:
    """Opening /mapview with NO id must still carry rings + the colour legend.

    Rings used to appear only after a pin tap, so the page reached from the menu
    was a handful of blue dots with no rings and no colour key — which reads as
    missing data rather than as a missing tap. And the ring payload used to be a
    bare geometry with no `geometry` member, so the page could not frame the
    rings and the effective-reach scaling silently drew the full-size ring.
    """
    import json
    import re

    real_status = m.pasture_overlay_status
    m.pasture_overlay_status = lambda *a, **k: {
        "bounds": [[0.50, 37.20], [0.62, 37.29]], "available": True,
        "usable_pct": 62, "frac": {"green": 20, "dry": 42, "bare": 12},
        "blank_pct": 18,
    }
    try:
        html = mapview_page(lat=0.3525, lon=37.583, species="cattle", lang="swa",
                            id=None, numbered=None, name=None,
                            interval="every_2_3_days", focus=0, graze=0, t=None)
    finally:
        m.pasture_overlay_status = real_status
    body = html.body.decode("utf-8", errors="replace")
    data = json.loads(re.search(r"const D = (\{.*?\});\n", body, re.S).group(1))

    assert data["main_id"], "the nearest water point should be focused on load"
    assert len(data["rings"]) == 3, data["rings"]
    for ring in data["rings"]:
        assert ring["geojson"]["type"] == "Feature", "rings must be Features"
        assert ring["geojson"]["geometry"]["type"] in ("Polygon", "MultiPolygon")
        assert ring["color"]
    assert data["overlay"] and data["overlay"]["frac"], "legend needs the shares"
    # 2-3 day watering makes the cattle ring 9.8 km; what is drawn must be that
    # ring, not the 7 km stored one.
    cattle = next(r for r in data["rings"] if r["species"] == "cattle")
    coords = cattle["geojson"]["geometry"]["coordinates"][0]
    width_km = (max(c[0] for c in coords) - min(c[0] for c in coords)) * 111.32
    assert abs(cattle["km"] - 9.8) < 0.05, cattle["km"]
    assert abs(width_km - 2 * cattle["km"]) / (2 * cattle["km"]) < 0.08, width_km

    # Graze mode records where the herd grazed by tapping: it must not be
    # auto-focused onto a water point.
    html2 = mapview_page(lat=0.3525, lon=37.583, species="cattle", lang="swa",
                         id=None, numbered=None, name=None, interval="daily",
                         focus=0, graze=1, t="tok")
    data2 = json.loads(re.search(r"const D = (\{.*?\});\n",
                                 html2.body.decode("utf-8", errors="replace"),
                                 re.S).group(1))
    assert data2["main_id"] is None and not data2["rings"], "graze mode must not focus"
    print(f"auto-focus OK (rings {len(data['rings'])}, overlays on load, "
          f"cattle ring {cattle['km']} km drawn {width_km:.1f} km wide)")


def test_overview_cache() -> None:
    """The overview COG must be downloaded once, not once per call.

    Every map surface reads the same ~12 MB object; re-fetching it per call made
    /mapview take ~14 s, long enough that a phone showed a map with no rings.
    """
    from app.services import raster_read

    calls = {"n": 0}

    def fake(wsid, bands=None, max_dim=512):
        calls["n"] += 1
        if wsid == "nowhere":
            return None
        return np.zeros((3, 8, 8), dtype=np.float32), object()

    real = raster_read._read_overview_array_uncached
    raster_read._read_overview_array_uncached = fake
    raster_read.clear_overview_cache()
    try:
        a1 = raster_read.read_overview_array("point-a", bands=[1, 3, 4])
        a2 = raster_read.read_overview_array("point-a", bands=[1, 3, 4])
        raster_read.read_overview_array("point-b", bands=[1, 3, 4])
        assert raster_read.read_overview_array("nowhere", bands=[1]) is None
        assert calls["n"] == 3, f"{calls['n']} loads for 4 reads"
        assert a1 is a2, "the cached array must be reused"
        assert not a1[0].flags.writeable, "cached arrays are shared: keep them read-only"
    finally:
        raster_read._read_overview_array_uncached = real
        raster_read.clear_overview_cache()
    print("overview cache OK (1 download per point+bands+size)")


def test_overview_disk_cache() -> None:
    """The 8 MB object is kept on disk too, so a FRESH worker does not refetch it.

    The in-memory cache lives inside one worker process; without the disk copy a
    second worker (or a restart) paid the full ~20-30 s download again.
    """
    import tempfile
    from pathlib import Path

    from app.services import raster_read as rr

    real_dir = rr._OVERVIEW_DISK_DIR
    rr._OVERVIEW_DISK_DIR = Path(tempfile.mkdtemp()) / "overview-cache"
    try:
        rr._overview_disk_write("pt", "etag1234567890abcdef", b"abc")
        assert rr._overview_disk_read("pt", "etag1234567890abcdef", 3) == b"abc"
        assert rr._overview_disk_read("pt", "etag1234567890abcdef", 99) is None, \
            "a truncated/partial copy must be refetched"
        assert rr._overview_disk_read("pt", "a-different-etag", 3) is None, \
            "a rebuilt object must not be served from the old copy"
        assert rr._overview_disk_read("pt", None, None) == b"abc", \
            "with R2 unreachable the last copy is better than no map"
        rr._overview_disk_write("pt", "etagZZZZZZZZZZZZZZZZ", b"defg")
        assert rr._overview_disk_read("pt", "etagZZZZZZZZZZZZZZZZ", 4) == b"defg"
        assert len(rr._overview_disk_files("pt")) == 1, "old copies must be evicted"
    finally:
        rr._OVERVIEW_DISK_DIR = real_dir
    print("overview disk cache OK (one file per point, ETag-checked)")


def test_pasture_status_does_not_wait_for_the_satellite() -> None:
    """The map page must not be held hostage by a cold 8 MB raster download.

    Rings come from the database in milliseconds; the pasture percentages need the
    satellite object, which takes 20-30 s cold on the deployed instance. The page
    reports the layer as present (it exists) and leaves the shares for next time.
    The advisory, which quotes the shares, still blocks.
    """
    from app.services import raster_read

    real_warm = raster_read.read_overview_array_warm
    real_blocking = raster_read.read_overview_array
    real_exists = raster_read.overview_exists
    raster_read.read_overview_array_warm = lambda *a, **k: None
    raster_read.read_overview_array = lambda *a, **k: None
    raster_read.overview_exists = lambda wid: True
    ws_id = "88bc1e17-a5f6-4c4d-a410-e551e4af7e54"
    try:
        st = m.pasture_overlay_status(ws_id, "camel", "daily")
        assert st and st["available"] is True, st
        assert st["frac"] is None and st["usable_pct"] is None, "no invented percentages"
        assert st["bounds"], "the layer still has to be placed on the map"
        assert m.pasture_overlay_status(ws_id, "camel", "daily",
                                        warm=False)["available"] is False, \
            "the advisory path keeps its old, blocking behaviour"
    finally:
        raster_read.read_overview_array_warm = real_warm
        raster_read.read_overview_array = real_blocking
        raster_read.overview_exists = real_exists
    print("map page never waits for the satellite OK (present + shares next time)")


if __name__ == "__main__":
    test_mapview_pages()
    test_autofocus_shows_rings_and_legend()
    test_overview_cache()
    test_overview_disk_cache()
    test_pasture_status_does_not_wait_for_the_satellite()
    test_fit_view_real()
    print("\nmapview + fit-overview tests OK.")
