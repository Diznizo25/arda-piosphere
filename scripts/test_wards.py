"""Which ward is this? — tests for the coordinate → ward lookup.

Pure: reads the ward polygons we ship, no DB, no network. Run:
    python scripts/test_wards.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.services import wards  # noqa: E402

# --- 1) we ship the polygons, and we only load the small per-ward files -------
assert wards.SKIP_FILES == {"kenya_adm3_wards.geojson"}, \
    "the 5 MB national file must never be loaded into the web service"
names = wards.known_wards()
print(f"wards loaded: {len(names)} -> {names}")
assert len(names) >= 10, f"expected at least Isiolo's ten wards, got {names}"
for expected in ("Oldonyiro", "Burat", "Ngare Mara", "Kinna", "Garbatulla", "Sericho",
                 "Chari", "Cherab", "Wabera", "Bulla Pesa"):
    assert expected in names, f"{expected} is missing from the ward files"

# --- 2) real coordinates people have actually sent us ------------------------
oldonyiro = wards.ward_for(0.5854411, 36.9915414)      # inside Oldonyiro's rings
assert oldonyiro.ward == "Oldonyiro", oldonyiro
assert oldonyiro.context == wards.RURAL, oldonyiro
assert oldonyiro.county == "Isiolo", oldonyiro
print(f"Oldonyiro point  -> {oldonyiro.ward} ({oldonyiro.context})")

lengwenyi = wards.ward_for(0.5669, 37.2402)             # Lengwenyi well
assert lengwenyi.known, f"Lengwenyi well should fall inside a ward we ship: {lengwenyi}"
print(f"Lengwenyi well   -> {lengwenyi.ward} ({lengwenyi.context})")

town = wards.ward_for(0.3540, 37.5820)                  # Isiolo town centre
assert town.ward in ("Wabera", "Bulla Pesa"), town
assert town.context == wards.PERI_URBAN, town
print(f"Isiolo town      -> {town.ward} ({town.context})")

outside = wards.ward_for(-1.2921, 36.8219)              # Nairobi
assert outside.ward is None and outside.context == wards.UNKNOWN, outside
assert not outside.known, outside
print(f"Nairobi          -> {outside.ward} ({outside.context})")

assert not wards.ward_for(None, None).known, "missing coordinates must not guess"

# --- 3) a herder's typed ward name, matched only against what we ship --------
assert wards.match_ward("burat") == "Burat"
assert wards.match_ward("  BURAT  ") == "Burat"
assert wards.match_ward("Bulla Pesa") == "Bulla Pesa"
assert wards.match_ward("nairobi") is None, "a ward we do not serve must be refused"
assert wards.match_ward("xxx") is None
assert wards.match_ward("") is None and wards.match_ward(None) is None
print("ward names match case-insensitively, and invented wards are refused")

# --- 4) the pin flow uses it, and confirms the ward back to the herder -------
src = io.open("app/routers/whatsapp.py", encoding="utf-8").read()
assert "wards.ward_for(lat, lon)" in src, "the pin flow must derive the ward itself"
assert "ward=hit.ward, county=hit.county" in src, \
    "the derived ward must be stored on the water source"
assert 'conversation.set_state(phone, "pin.ward"' in src, \
    "an unplaceable point must ask the herder, not store an empty ward"
assert 'elif state == "pin.ward":' in src, "and that answer must be handled"
assert "water_sources.set_ward(" in src, "the herder's ward must be written to the point"
assert "ward ya {hit.ward}" in src or "ward {hit.ward}" in src, \
    "the confirmation must state the ward back to him"
assert "wards.PERI_URBAN" in src, \
    "peri-urban wards (Isiolo town) must take the different branch"
ws_src = io.open("app/services/water_sources.py", encoding="utf-8").read()
assert "def set_ward(" in ws_src, "the service layer needs set_ward"
print("pin flow: derives the ward, states it back, asks when it cannot tell")

print("\nward lookup tests OK")
