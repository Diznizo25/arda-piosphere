"""Build config/rivers.geojson — named rivers/streams as LINES (not points).

Why this exists: water_sources.geom is geometry(Point) and piosphere_zones are
circles, so a river — which is a LONG water body — cannot be represented as a
ring around one point. Rivers are stored here as LineStrings, and the grazing
zones for a river are computed as distance ribbons ALONG the line
(app/services/river_zones.py): every metre of riverbank has water, so the
animal's reach is measured from the whole line, not from a dot.

Usage:
  python scripts/build_rivers.py [--bbox south,west,north,east] [--dry-run]
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build_rivers")

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

# Isiolo county + the lake/river systems herders move between.
DEFAULT_BBOX = "-0.35,36.7,1.35,38.6"  # south,west,north,east


def fetch(bbox: str) -> list[dict]:
    query = f"""
    [out:json][timeout:180];
    (
      way["waterway"~"^(river|stream)$"]["name"]({bbox});
      way["natural"="water"]["name"]["water"~"^(river|canal)$"]({bbox});
    );
    out geom tags;
    """
    last_err: Exception | None = None
    for url in OVERPASS_URLS:
        try:
            resp = httpx.post(
                url, data={"data": query}, timeout=240,
                headers={"User-Agent": "arda-piosphere/1.0 (river gazetteer)"},
            )
            resp.raise_for_status()
            return resp.json().get("elements", [])
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("Overpass mirror %s failed: %s", url, e)
    raise RuntimeError(f"all Overpass mirrors failed: {last_err}")


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    bbox = sys.argv[sys.argv.index("--bbox") + 1] if "--bbox" in sys.argv else DEFAULT_BBOX
    elements = fetch(bbox)

    try:
        from shapely.geometry import LineString  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        LineString = None  # type: ignore[assignment]

    features: list[dict] = []
    for el in elements:
        tags = el.get("tags") or {}
        name = (tags.get("name") or "").strip()
        geom_pts = el.get("geometry") or []
        coords = [[p["lon"], p["lat"]] for p in geom_pts
                  if p.get("lon") is not None and p.get("lat") is not None]
        if not name or len(coords) < 2:
            continue
        kind = "river" if tags.get("waterway") == "river" or tags.get("water") == "river" \
            else "stream"
        if LineString is not None:
            line = LineString(coords)
            # 0.0005 deg ~ 55 m: keeps the file small, keeps the shape recognisable.
            line = line.simplify(0.0005, preserve_topology=False)
            coords = [[round(x, 5), round(y, 5)] for x, y in line.coords]
        features.append({
            "type": "Feature",
            "properties": {"name": name[:60], "kind": kind},
            "geometry": {"type": "LineString", "coordinates": coords},
        })

    # Longest first so the big rivers survive any later truncation.
    features.sort(key=lambda f: -len(f["geometry"]["coordinates"]))
    names = {f["properties"]["name"] for f in features}
    log.info("Collected %d river segments (%d distinct names).", len(features), len(names))

    out_path = Path("config/rivers.geojson")
    if dry_run:
        for f in features[:10]:
            log.info("  %s (%s) %d pts", f["properties"]["name"],
                     f["properties"]["kind"], len(f["geometry"]["coordinates"]))
        return 0
    out_path.write_text(json.dumps(
        {"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8")
    log.info("Wrote %s (%d segments).", out_path, len(features))
    return 0


if __name__ == "__main__":
    sys.exit(main())
