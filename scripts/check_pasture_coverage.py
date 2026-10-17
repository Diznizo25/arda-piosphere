"""How much of each water point's reach can the satellite actually read?

Answers "why are some pixels not coloured?" with a number per point, from the
DEPLOYED server (it can reach the database and the COGs; a laptop often cannot).

Two numbers per point, both measured from the same in-ring mask the overlay clips to:
  * blank  — share of the herder's reach ring we could not read (cloud/shadow/no pass)
  * usable — share classified as pasture (green + dry); the rest is bare

Read-only. One /mapview request per point, so it costs a few seconds each.

    python scripts/check_pasture_coverage.py                 # all points, cattle
    python scripts/check_pasture_coverage.py --species camel
    python scripts/check_pasture_coverage.py --base http://localhost:8000

Exit code is non-zero only when no point could be measured at all.
"""
from __future__ import annotations

import argparse
import re
import sys

import httpx

DEFAULT_BASE = "https://arda-piosphere.onrender.com"


def grab(body: str, key: str) -> float | None:
    """Pull one numeric field out of the overlay payload embedded in the page."""
    m = re.search(r'"%s":\s*([0-9.]+|null)' % key, body)
    if not m or m.group(1) == "null":
        return None
    return float(m.group(1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--species", default="cattle")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    src = httpx.get(f"{base}/water-sources", timeout=90).json()["water_sources"]
    print(f"{len(src)} water points, species={args.species}\n")

    rows: list[tuple[str, float | None, float | None]] = []
    for i, w in enumerate(src, 1):
        name = (w.get("name") or f"{w.get('water_type') or 'point'} {i}")[:26]
        try:
            r = httpx.get(f"{base}/mapview", timeout=180, params={
                "lat": w["lat"], "lon": w["lon"], "id": w["id"],
                "species": args.species, "lang": "swa"})
        except Exception as e:  # noqa: BLE001 — report and keep going
            print(f"  {name:<27} ERROR {type(e).__name__}")
            continue
        b = r.text
        blank, usable = grab(b, "blank_pct"), grab(b, "usable_pct")
        fr = {k: grab(b, k) for k in ("green", "dry", "bare")}
        rows.append((name, blank, usable))
        print(f"  {name:<27} blank {str(blank):>5}%  usable {str(usable):>5}%  "
              f"(green {fr['green']}, dry {fr['dry']}, bare {fr['bare']})")

    blanks = [b for _, b, _ in rows if b is not None]
    if not blanks:
        print("\nno point could be measured — is the service up?")
        return 1
    print(f"\nblank share of the ring across {len(blanks)} points: "
          f"min {min(blanks)}%  max {max(blanks)}%  mean {round(sum(blanks) / len(blanks))}%")
    print("A high share means clouds/shadow/no clean pass — the honest limit of what")
    print("we can claim about pasture there, stated rather than coloured over.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
