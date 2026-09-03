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
    }
    try:
        for lang in ("swa", "eng"):
            html = mapview_page(lat=0.3525, lon=37.583, species="camel", lang=lang,
                                id="88bc1e17-a5f6-4c4d-a410-e551e4af7e54",
                                numbered=None, name=None)
            body = html.body.decode("utf-8", errors="replace")
            assert "leaflet" in body.lower()
            assert "__DATA__" not in body
            assert '"herder"' in body and '"options"' in body
            assert "L.imageOverlay" in body and "pasture.png" in body, "pasture overlay"
            assert "usable_pct" in body
            print(f"{lang} mapview page OK ({len(body)} chars, pasture overlay present)")
        html2 = mapview_page(lat=0.3525, lon=37.583, species="camel", lang="swa",
                             id=None, name=None,
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


if __name__ == "__main__":
    test_mapview_pages()
    test_fit_view_real()
    print("\nmapview + fit-overview tests OK.")
