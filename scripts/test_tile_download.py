"""Test downloading a single tile from the problematic asset to isolate the connection issue."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ee
import requests

from app.services.gee_auth import init_earth_engine

init_earth_engine()

a = "projects/protean-tooling-466007-r0/assets/piosphere/c3a76f7e-f4db-478a-a75a-fdc661981ff5"
img = ee.Image(a)

coords = img.geometry().bounds().coordinates().getInfo()
xs = [c[0] for c in coords[0]]
ys = [c[1] for c in coords[0]]
min_x, min_y, max_x, max_y = min(xs), min(ys), max(xs), max(ys)
print(f"bounds: {min_x}, {min_y}, {max_x}, {max_y}")

dx = (max_x - min_x) / 5
dy = (max_y - min_y) / 5

# Test tile index 1 (i=0, j=1) - the one that keeps failing
for tile_idx in [1, 0, 2]:
    i = tile_idx % 5
    j = tile_idx // 5
    x0 = min_x + i * dx
    x1 = min_x + (i + 1) * dx
    y0 = min_y + j * dy
    y1 = min_y + (j + 1) * dy
    region = {
        "type": "Polygon",
        "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
    }
    print(f"\n--- tile {tile_idx} (i={i}, j={j}) ---")
    try:
        dl = ee.data.getDownloadId({
            "image": img,
            "region": region,
            "scale": 10,
            "crs": "EPSG:4326",
            "format": "GEO_TIFF",
        })
        url = ee.data.makeDownloadUrl(dl)
        print(f"url: {url[:120]}")
        for attempt in range(1, 4):
            try:
                with requests.Session() as s:
                    r = s.get(url, timeout=120)
                    r.raise_for_status()
                    print(f"  attempt {attempt}: OK, {len(r.content)} bytes")
                    break
            except Exception as e:
                print(f"  attempt {attempt}: FAILED {type(e).__name__}: {e}")
                time.sleep(5)
    except Exception as e:
        print(f"  getDownloadId FAILED: {type(e).__name__}: {e}")
