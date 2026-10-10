"""Split the national ward file into one GeoJSON per Isiolo ward.

The rollout pipeline takes ONE ward polygon per run:

    python scripts/import_water_sources.py --boundary config/wards/<ward>.geojson \
        --ward "<Ward>" --county "Isiolo" --source both
    python scripts/generate_piosphere_zones.py --ward "<Ward>"
    python scripts/gee_export_to_asset.py --ward "<Ward>"
    python scripts/transfer_assets_to_r2.py --ward "<Ward>"

This produces those per-ward files from the national ADM3 file we already ship, so no
download and no GIS dependency is needed.

Usage:
    python scripts/split_wards.py                 # Isiolo county (default)
    python scripts/split_wards.py --list          # show what matched, write nothing
    python scripts/split_wards.py --ward Chari    # one ward only
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

SRC = Path("config/wards/kenya_adm3_wards.geojson")
OUT_DIR = Path("config/wards")

# Isiolo County's ten wards (IEBC). Names are matched case-insensitively against the
# shapeName property, because the national file carries no county column.
ISIOLO_WARDS = ["Wabera", "Bulla Pesa", "Ngare Mara", "Burat", "Oldonyiro",
                "Kinna", "Garbatulla", "Sericho", "Chari", "Cherab"]


def load_features(path: Path) -> list[dict]:
    data = json.loads(io.open(path, encoding="utf-8").read())
    return data["features"] if data.get("type") == "FeatureCollection" else [data]


def slug(name: str) -> str:
    return name.lower().replace(" ", "_")


def match(features: list[dict], ward: str) -> dict | None:
    for f in features:
        name = str((f.get("properties") or {}).get("shapeName") or "")
        if name.strip().lower() == ward.strip().lower():
            return f
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=SRC)
    ap.add_argument("--ward", help="Split only this ward")
    ap.add_argument("--list", action="store_true", help="Report matches, write nothing")
    args = ap.parse_args()

    if not args.source.exists():
        print(f"missing source: {args.source}")
        return 1
    features = load_features(args.source)
    print(f"{args.source}: {len(features)} ADM3 polygons")

    wards = [args.ward] if args.ward else ISIOLO_WARDS
    found, missing = [], []
    for ward in wards:
        feature = match(features, ward)
        if feature is None:
            missing.append(ward)
            continue
        geom = feature.get("geometry") or {}
        ring = (geom.get("coordinates") or [[]])[0]
        xs = [c[0] for c in ring] if ring and isinstance(ring[0], (list, tuple)) else []
        ys = [c[1] for c in ring] if ring else []
        span = (f"{max(xs) - min(xs):.3f} x {max(ys) - min(ys):.3f} deg"
                if xs and ys else "no coords")
        found.append((ward, geom.get("type"), len(ring), span))
        if args.list:
            continue
        target = OUT_DIR / f"{slug(ward)}.geojson"
        payload = {"type": "FeatureCollection", "features": [{
            "type": "Feature",
            "properties": {"ward": ward, "county": "Isiolo",
                           "source": args.source.name},
            "geometry": geom,
        }]}
        target.write_text(json.dumps(payload), encoding="utf-8")
        print(f"  wrote {target}  ({geom.get('type')}, {len(ring)} vertices, {span})")

    print(f"\nmatched {len(found)}/{len(wards)}")
    for ward, gtype, vertices, span in found:
        print(f"  OK  {ward:<14} {gtype:<14} {vertices:>5} vertices  {span}")
    if missing:
        print(f"  MISSING from the national file: {missing}")
        print("  (check the spelling against shapeName, or digitise the boundary - see "
              "config/wards/README.md)")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
