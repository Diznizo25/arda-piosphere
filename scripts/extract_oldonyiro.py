"""Extract the Oldonyiro ward boundary from the Kenya ADM3 GeoJSON and save it
as a standalone GeoJSON Feature in config/wards/oldonyiro.geojson.
"""
import json
from pathlib import Path

SRC = Path("config/wards/kenya_adm3_wards.geojson")
DEST = Path("config/wards/oldonyiro.geojson")


def main():
    data = json.loads(SRC.read_text())
    matches = [
        f for f in data["features"]
        if f["properties"].get("shapeName") == "Oldonyiro"
    ]
    if not matches:
        raise SystemExit("Oldonyiro ward not found in ADM3 data")
    feature = matches[0]
    # Keep a clean properties set
    feature["properties"] = {"name": "Oldonyiro", "county": "Isiolo"}
    out = {"type": "FeatureCollection", "features": [feature]}
    DEST.write_text(json.dumps(out))
    print(f"Saved Oldonyiro boundary to {DEST}")
    print(f"Geometry type: {feature['geometry']['type']}")


if __name__ == "__main__":
    main()
