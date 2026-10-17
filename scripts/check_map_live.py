"""Does the DEPLOYED map still tell the truth? One command, read-only.

Fetches the live /mapview page for Lengwenyi well and checks the SERVED HTML: that
satellite loads first, that the deep-zoom claim is clamped to the measured z18, that
the blank-pixel explanation is there, and that the blank share arrives as a real
number (not just as code that could display one).

This exists because a unit test can pass while the page tells the herder nothing: the
overlay payload is an allow-list, so blank_pct was computed, dropped, and shipped
invisible. Only a check against the served page catches that class of bug.

Run from any machine with internet:
    python scripts/check_map_live.py
Exit code is non-zero if any check fails.
"""
from __future__ import annotations

import re
import sys

import httpx

BASE = "https://arda-piosphere.onrender.com"
WS = "88bc1e17-a5f6-4c4d-a410-e551e4af7e54"   # Lengwenyi well, Oldonyiro

ok = 0
fail: list[str] = []


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok
    if cond:
        ok += 1
        print(f"  PASS  {label}{(' — ' + extra) if extra else ''}")
    else:
        fail.append(label)
        print(f"  FAIL  {label}{(' — ' + extra) if extra else ''}")


r = httpx.get(f"{BASE}/mapview", params={"lat": 0.3525, "lon": 37.583, "id": WS,
                                         "species": "camel", "lang": "swa",
                                         "focus": 1}, timeout=90)
print(f"page: HTTP {r.status_code}, {len(r.text)} chars")
check("page served", r.status_code == 200)
body = r.text

# 1) the real ground loads first: satellite, not the street drawing
check("satellite layer is the one added to the map", "esriTiles.addTo(map)" in body)
check("street map still offered in the switcher", "tile.openstreetmap.org" in body)
check("satellite listed first in the switcher",
      "[TXT.satBase || 'Satellite']: esriTiles" in body)
check("exactly one base-map switcher", body.count("L.control.layers(") == 1,
      f"{body.count('L.control.layers(')} found")
check("street map is no longer the default", "osmTiles).addTo(map)" not in body)

# 2) the deep-zoom clamp (measured: Esri is real to z18 in Isiolo, placeholder above)
check("native zoom clamped to the measured z18", "ESRI_NATIVE = 18" in body and
      "maxNativeZoom: ESRI_NATIVE" in body)
check("stretched-imagery chip present", "g_stretch" in body)
check("close-up swap present", "ortho" in body and "g_close" in body)

# 3) blanks explained: why a pixel has no colour, and how much of his reach it is
check("legend explains blank pixels (we do not guess)", "no_read" in body and
      "no_guess" in body)
check("blank share is quantified in the payload (not just in the code)", "blank_pct" in body)
_bm = re.search(r'"blank_pct":\s*(\d+)', body)
check("blank share is a real percentage of his ring",
      bool(_bm) and 0 < int(_bm.group(1)) < 100,
      f"blank_pct={_bm.group(1) if _bm else 'missing'}")
check("legend explains the reach-line clip", "ring_clip" in body)
check("legend still names the pasture colours",
      "pastureCols.green" in body and "pastureCols.dry" in body and "pastureCols.bare" in body)

# 4) no ortho uploaded yet -> a clean 404, never a 500 or a broken page
o = httpx.get(f"{BASE}/map/{WS}/ortho.png", timeout=90, follow_redirects=True)
print(f"ortho.png: HTTP {o.status_code}")
check("missing close-up is a clean 404 (inert until one is uploaded)",
      o.status_code in (404, 400), f"HTTP {o.status_code}")

print(f"\n{ok} passed, {len(fail)} failed")
if fail:
    for f in fail:
        print("  failed:", f)
    sys.exit(1)
print("LIVE MAP OK — real ground first, blanks named as blanks, mapping switchable.")
