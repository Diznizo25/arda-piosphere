"""Decisive check: does the production map really show Isiolo at the herder's coords?

The viewport centre of the production PNG sits at a sub-pixel offset inside its
OSM tile. We rebuild that exact sub-region of the tile containing the herder's
lon/lat and compare it against the production PNG's centre crop. Low RMS = the
production map is correctly geolocated (no 130km Machakos-style shift).
"""
from __future__ import annotations

import io
import math
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PIL import Image

from app.services.map_renderer import _mercator_x, _mercator_y

LAT, LON = 0.35, 37.58
ZOOM = 11
TILE = 256
IMG = 1024


def fetch_tile(z: int, x: int, y: int) -> Image.Image:
    req = urllib.request.Request(
        f"https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        headers={"User-Agent": "ArdaPiosphere-verify/0.1"},
    )
    return Image.open(io.BytesIO(urllib.request.urlopen(req, timeout=30).read())).convert("RGB")


def main() -> None:
    mpp = 156543.03392 * math.cos(math.radians(LAT)) / (2 ** ZOOM)
    cx, cy = _mercator_x(LON), _mercator_y(LAT)
    world_px = TILE * (2 ** ZOOM)
    mpp_world = 40075016.686 / world_px
    cx_px = world_px / 2 + cx / mpp_world
    cy_px = world_px / 2 - cy / mpp_world

    tcx = int(math.floor(cx_px / TILE))
    tcy = int(math.floor(cy_px / TILE))
    off_x = int(cx_px - tcx * TILE)
    off_y = int(cy_px - tcy * TILE)
    print(f"herder sits at offset ({off_x},{off_y}) inside tile {ZOOM}/{tcx}/{tcy}")

    tile = fetch_tile(ZOOM, tcx, tcy)
    # Sub-region of the tile that the production PNG's centre 256x256 shows.
    sx0, sy0 = off_x - 128, off_y - 128
    region = tile.crop((max(sx0, 0), max(sy0, 0),
                        min(sx0 + 256, 256), min(sy0 + 256, 256)))

    prod = Image.open("prod2.png").convert("RGB")
    w, h = prod.size
    crop = prod.crop((w // 2 - 128, h // 2 - 128, w // 2 + 128, h // 2 + 128))
    crop.save("prod_center_crop.png")
    region.save("tile_center_region.png")

    rms = float(np.sqrt(np.mean(
        (np.asarray(crop.resize(region.size)).astype(float)
         - np.asarray(region).astype(float)) ** 2)))
    print(f"RMS diff (prod centre vs rebuilt Isiolo tile region): {rms:.2f}")
    if rms < 25:
        print(">> MATCH: the production map IS correctly centered on Isiolo.")
    else:
        print(">> MISMATCH: map centre does not match the Isiolo tile — investigate.")


if __name__ == "__main__":
    main()
