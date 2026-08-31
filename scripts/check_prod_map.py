"""Download the production map and check whether OSM base tiles actually loaded.

If the image is mostly the beige fallback color (228,224,216), tiles failed on
Render and the herder sees circles with no landmarks at all.
"""
from __future__ import annotations

import io
import sys

import httpx
from PIL import Image

URL = ("https://arda-piosphere.onrender.com/map/"
       "d6734528-8e63-4c69-8d02-a6e5a004b0f5.png"
       "?lat=0.35&lon=37.58&species=camel&pasture=1&v=8")


def main() -> None:
    r = httpx.get(URL, timeout=120)
    print("status:", r.status_code, "bytes:", len(r.content))
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    img = img.resize((256, 256))
    px = list(img.getdata())
    n = len(px)
    beige = sum(1 for p in px if abs(p[0]-228) < 12 and abs(p[1]-224) < 12 and abs(p[2]-216) < 12)
    green = sum(1 for p in px if p[1] > p[0] + 20 and p[1] > p[2] + 20)
    red = sum(1 for p in px if p[0] > 150 and p[1] < 100)
    white = sum(1 for p in px if p[0] > 200 and p[1] > 200 and p[2] > 200)
    print(f"beige(fallback): {100*beige/n:.1f}%  green(pasture): {100*green/n:.1f}%  "
          f"red(bare): {100*red/n:.1f}%  white: {100*white/n:.1f}%")
    # color diversity: if tiles loaded, there should be lots of distinct colors
    distinct = len(set(px[::8]))
    print(f"distinct colors (sampled): {distinct}")
    if beige / n > 0.4:
        print(">> Base map looks BLANK/beige -> OSM tiles are NOT loading on Render")
    elif distinct > 50:
        print(">> Base map has varied content -> OSM tiles ARE loading")
    else:
        print(">> Ambiguous")


if __name__ == "__main__":
    main()
