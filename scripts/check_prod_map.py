"""Download the production map and check whether OSM base tiles actually loaded.

If the image is mostly the beige fallback color (228,224,216), tiles failed on
Render and the herder sees circles with no landmarks at all.

Usage:
  python scripts/check_prod_map.py                  # first live water point
  python scripts/check_prod_map.py <water_source_id> [species]

The hard-coded water source this used to point at no longer exists, which is why it
now picks a real one from the database (and why it 404s if you pass a dead id).
"""
from __future__ import annotations

import io
import sys

sys.path.insert(0, ".")

import httpx
from PIL import Image

BASE = "https://arda-piosphere.onrender.com"


def build_url(water_source_id: str | None, species: str) -> str:
    if water_source_id:
        from app.services.water_sources import list_water_sources

        match = next((w for w in list_water_sources()
                      if str(w.id) == water_source_id), None)
        if match is None:
            raise SystemExit(f"no such water_source_id: {water_source_id}")
        lat, lon = match.lat, match.lon
    else:
        from app.services.water_sources import list_water_sources

        first = list_water_sources()[0]
        water_source_id, lat, lon = first.id, first.lat, first.lon
    return (f"{BASE}/map/{water_source_id}.png"
            f"?lat={lat}&lon={lon}&species={species}&pasture=1&lang=swa&v=8")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    water_source_id = args[0] if args else None
    species = args[1] if len(args) > 1 else "camel"
    url = build_url(water_source_id, species)
    print("url:", url)
    r = httpx.get(url, timeout=120)
    print("status:", r.status_code, "bytes:", len(r.content))
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    print("size:", img.size)
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
