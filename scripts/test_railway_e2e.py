"""
Step 5 end-to-end validation against the deployed Render app.

Hits the live health endpoints and the advisory endpoint for a real location
inside an Oldonyiro water source's species rings, and validates the response
structure. Run it from any machine with internet:

  python scripts/test_railway_e2e.py https://arda-piosphere.onrender.com

For each species (cattle/shoat/camel) at a known water-source location it
prints the returned advisory and checks that the response is well-formed.
"""
from __future__ import annotations

import sys
from urllib.parse import urljoin

import requests

# 151f4aa6 water point in Oldonyiro — sits inside all three of its own
# species rings, so an advisory should always be found here.
TEST_LON, TEST_LAT = 36.9915414, 0.5854411
TEST_SPECIES = ["cattle", "shoat", "camel"]
TEST_LANGUAGE = "swahili"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/test_railway_e2e.py <APP_BASE_URL>")
        return 2
    base = sys.argv[1].rstrip("/")
    print(f"Testing deployed app: {base}\n")

    failed = 0

    # 1. Health endpoints
    for name, path in [("r2", "/health/r2"), ("db", "/health/db"), ("gee", "/health/gee")]:
        try:
            r = requests.get(urljoin(base, path), timeout=20)
            ok = r.status_code == 200
            check(f"/health/{name}", ok, f"HTTP {r.status_code}")
            if not ok:
                failed += 1
        except Exception as e:  # noqa: BLE001
            check(f"/health/{name}", False, type(e).__name__)
            failed += 1

    # 2. Advisory endpoint for each species at the known location
    for species in TEST_SPECIES:
        try:
            r = requests.post(
                urljoin(base, "/advisory"),
                json={"lat": TEST_LAT, "lon": TEST_LON, "species": species, "language": TEST_LANGUAGE},
                timeout=30,
            )
            body = r.json()
            ok = r.status_code == 200 and body.get("found") is True and bool(body.get("message"))
            detail = (
                f"HTTP {r.status_code} | "
                f"found={body.get('found')} | "
                f"water={body.get('water_source_id', '')[:8]} "
                f"@{body.get('distance_km')}km | "
                f"forage={body.get('forage_condition')} | "
                f"water_rel={body.get('water_reliability')} | "
                f"msg={body.get('message', '')[:60]}..."
            )
            check(f"/advisory {species}", ok, detail)
            if not ok:
                failed += 1
        except Exception as e:  # noqa: BLE001
            check(f"/advisory {species}", False, type(e).__name__)
            failed += 1

    print(f"\n{'ALL CHECKS PASSED' if failed == 0 else f'{failed} CHECK(S) FAILED'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
