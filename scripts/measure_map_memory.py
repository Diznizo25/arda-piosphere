"""Measure baseline + peak RSS of the map render path with mocked data (fast, no network)."""
from __future__ import annotations

import gc
import json
import sys
import types

import numpy as np
import psutil
from PIL import Image

sys.path.insert(0, ".")

from app.services import map_renderer as m  # noqa: E402
from app.services import raster_read, water_sources as ws_mod  # noqa: E402


def rss() -> float:
    return psutil.Process().memory_info().rss / 1e6


def circle(lon, lat, r):
    import math

    c = []
    for i in range(36):
        a = 2 * math.pi * i / 36
        c.append([lon + r * math.cos(a), lat + r * math.sin(a)])
    return {"type": "Polygon", "coordinates": [c + [c[0]]]}


def main() -> None:
    print(f"baseline RSS: {rss():.0f} MB")
    ws = types.SimpleNamespace(id="x", lon=37.5725, lat=0.3525, ward="Isiolo", county="Isiolo")
    zones = [{"species": "camel", "radius_km": 25.0,
              "geojson": json.dumps(circle(37.5725, 0.3525, 0.22))}]
    ws_mod.list_water_sources = lambda: [ws]
    ws_mod.zones_for_water_source = lambda wid: zones
    m._fetch_tile = lambda z, x, y: Image.new("RGB", (256, 256), (242, 239, 233))

    h = w = 256
    ndvi = np.full((h, w), 0.12); ndvi[20:80, 20:80] = 0.45
    satvi = np.full((h, w), 0.10); satvi[20:80, 20:80] = 0.13
    bsi = np.full((h, w), 0.20); bsi[20:80, 20:80] = 0.18
    arr = np.stack([ndvi, satvi, bsi])

    class FT:
        c = 37.40
        f = 0.45
        a = 0.005
        e = -0.005

    raster_read.read_overview_array = lambda wid, *a, **k: (arr, FT())

    peak = 0.0
    for i in range(3):
        png = m.render_rings_png("x", herder_lon=37.58, herder_lat=0.35,
                                 species="camel", pasture=True, lang="swa")
        gc.collect()
        now = rss()
        peak = max(peak, now)
        print(f"render {i + 1}: png={len(png)} bytes  rss={now:.0f} MB")
    print(f"PEAK RSS during render: {peak:.0f} MB (budget on 512MB instance: ~350MB)")


if __name__ == "__main__":
    main()
