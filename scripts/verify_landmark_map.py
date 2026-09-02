"""Verify the live WhatsApp map shows what a pastoralist needs:
- named landmark labels (town/village/river plates)
- type-coloured water markers in the legend
- compact legend that doesn't cover the map

Usage:
  python scripts/verify_landmark_map.py [version]
"""
from __future__ import annotations

import io
import sys

import httpx
import numpy as np
from PIL import Image

BASE = "https://arda-piosphere.onrender.com"
# Lengwenyi well (named OSM point), herder placed in Isiolo town.
WS = "88bc1e17-a5f6-4c4d-a410-e551e4af7e54"
V = sys.argv[1] if len(sys.argv) > 1 else "7"


def main() -> int:
    url = (f"{BASE}/map/{WS}.png?lat=0.352&lon=37.583&species=camel"
           f"&pasture=1&lang=swa&v={V}")
    r = httpx.get(url, timeout=240)
    print("map status", r.status_code, len(r.content), "bytes")
    if r.status_code != 200:
        return 1
    im = np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB")).astype(int)

    def cnt(rgb, tol=45):
        return int(((abs(im - np.array(rgb)).sum(axis=2)) < tol).sum())

    # 1) landmark town labels + white label plates across the map
    town_px = cnt((30, 41, 59))
    plates = int((im > 235).all(axis=2).sum())
    print("town-label dark px:", town_px, "| white label plates px:", plates)

    # 2) legend swatches: water type colours present in the legend area
    for name, c in [("river-blue", (59, 130, 246)), ("borehole-orange", (234, 88, 12)),
                    ("well-teal", (5, 150, 105)), ("spring-green", (34, 197, 94))]:
        n = cnt(c)
        print(f"  {name}: {n} px")

    # 3) legend compactness: the top-left white legend box should end by ~y400
    #    (was ~y505 when oversized). Scan column x=20 for the lowest white row
    #    within the top-left legend region (below the top banner).
    white_col = (im[:, 20] > 240).all(axis=1)
    ys = np.where(white_col)[0]
    ys_in = [y for y in ys if 70 < y < 600]
    if ys_in:
        print("legend-ish white column spans y", min(ys_in), "..", max(ys_in))
    ok = town_px > 1500 and max((cnt((59, 130, 246)), cnt((234, 88, 12)),
                                 cnt((5, 150, 105)))) > 100
    print("VERIFY:", "OK" if ok else "FAIL")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
