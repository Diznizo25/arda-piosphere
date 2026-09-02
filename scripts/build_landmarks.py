"""Build config/landmarks.geojson — named landmarks a herder recognises.

Queries Overpass for named towns/villages, peaks, rivers and markets across
Isiolo county, and writes a small GeoJSON point gazetteer. The map renderer
uses it to (a) draw landmark labels on the map and (b) describe unnamed water
points as "near <landmark>" so every water point means something.

Usage:
  python scripts/build_landmarks.py [--bbox south,west,north,east] [--dry-run]
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build_landmarks")

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

# Isiolo county (approx). Broader than the water points so far so the gazetteer
# still helps when points are added elsewhere in the county.
DEFAULT_BBOX = "-0.35,36.7,1.35,38.6"  # south,west,north,east


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    bbox = sys.argv[sys.argv.index("--bbox") + 1] if "--bbox" in sys.argv else DEFAULT_BBOX
    query = f"""
    [out:json][timeout:180];
    (
      node["place"~"^(city|town|village|hamlet)$"]["name"]({bbox});
      node["natural"="peak"]["name"]({bbox});
      way["waterway"="river"]["name"]({bbox});
      node["amenity"~"^(marketplace)$"]["name"]({bbox});
      way["natural"="water"]["name"]["water"~"river|canal"]({bbox});
    );
    out center tags;
    """
    last_err: Exception | None = None
    for url in OVERPASS_URLS:
        try:
            resp = httpx.post(
                url, data={"data": query}, timeout=240,
                headers={"User-Agent": "arda-piosphere/1.0 (landmark gazetteer)"},
            )
            resp.raise_for_status()
            elements = resp.json().get("elements", [])
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("Overpass mirror %s failed: %s", url, e)
    else:
        raise RuntimeError(f"all Overpass mirrors failed: {last_err}")

    features = []
    for el in elements:
        tags = el.get("tags") or {}
        name = (tags.get("name") or "").strip()
        if not name:
            continue
        if el.get("type") == "node":
            lon, lat = el.get("lon"), el.get("lat")
        else:
            center = el.get("center") or {}
            lon, lat = center.get("lon"), center.get("lat")
        if lon is None or lat is None:
            continue
        kind = None
        place = tags.get("place")
        if place:
            kind = place  # city/town/village/hamlet
        elif tags.get("natural") == "peak":
            kind = "peak"
        elif tags.get("waterway") == "river" or tags.get("water") == "river":
            kind = "river"
        elif tags.get("amenity") == "marketplace":
            kind = "market"
        if not kind:
            continue
        features.append({
            "type": "Feature",
            "properties": {"name": name[:60], "kind": kind},
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
        })

    log.info("Collected %d landmarks.", len(features))
    kinds: dict[str, int] = {}
    for f in features:
        kinds[f["properties"]["kind"]] = kinds.get(f["properties"]["kind"], 0) + 1
    log.info("Kinds: %s", kinds)

    out_path = Path("config/landmarks.geojson")
    if dry_run:
        log.info("Dry run — not writing %s.", out_path)
        for f in features[:10]:
            log.info("  %s (%s) %.4f,%.4f", f["properties"]["name"],
                     f["properties"]["kind"], *f["geometry"]["coordinates"][::-1])
        return 0
    out_path.write_text(json.dumps(
        {"type": "FeatureCollection", "features": features}, ensure_ascii=False), encoding="utf-8")
    log.info("Wrote %s (%d landmarks).", out_path, len(features))
    return 0


if __name__ == "__main__":
    sys.exit(main())
