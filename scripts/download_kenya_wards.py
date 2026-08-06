"""Download Kenya ADM3 (ward) boundaries from geoBoundaries and validate.
Downloads to a temp file, validates JSON, then moves into place.
"""
import json
import sys
import urllib.request
from pathlib import Path

URL = "https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/KEN/ADM3/geoBoundaries-KEN-ADM3.geojson"
DEST = Path("config/wards/kenya_adm3_wards.geojson")
TMP = Path("config/wards/kenya_adm3_wards.geojson.tmp")


def main():
    print(f"Downloading {URL} ...")
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = resp.read()
    print(f"Downloaded {len(data)} bytes")

    # Validate JSON before moving into place
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as e:
        print(f"ERROR: downloaded file is not valid JSON: {e}")
        sys.exit(1)

    features = parsed.get("features", [])
    print(f"Valid JSON: {len(features)} features")
    if features:
        print(f"Sample properties: {features[0].get('properties')}")

    TMP.write_bytes(data)
    TMP.replace(DEST)
    print(f"Saved to {DEST}")


if __name__ == "__main__":
    main()
