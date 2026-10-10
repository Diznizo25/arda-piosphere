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

# --- 4) the pin flow confirms the ward BEFORE it registers anything ----------
src = io.open("app/routers/whatsapp.py", encoding="utf-8").read()
assert "wards.ward_for(lat, lon)" in src, "the pin flow must derive the ward itself"
assert "ward=ward, county=wards.COUNTY" in src, \
    "the confirmed ward must be the one stored on the water source"
assert "wards.match_ward(text)" in src, \
    "a ward he types must be matched against the wards we ship"

# The name step asks; it must not register. Registration lives in the confirm step.
name_branch = src.split('elif state == "pin.name":')[1].split(
    'elif state == "pin.ward_confirm":')[0]
assert "_start_pin_ward_check(" in name_branch, \
    "after the name we must ask about the ward, not write the point"
assert "_finish_pin_registration(" not in name_branch, \
    "the name step must not register the point before the ward is confirmed"

ask_fn = src.split("def _start_pin_ward_check(")[1].split("def _finish_pin_registration(")[0]
assert 'conversation.set_state(phone, "pin.ward_confirm"' in ask_fn, \
    "the derived ward must be put to him for confirmation"
assert "water_sources.create_water_source(" not in ask_fn, \
    "nothing may be written before he confirms"
assert "send_quick_reply_buttons" in ask_fn, "one tap to confirm, not a typed word"
assert 'conversation.set_state(phone, "pin.ward"' in ask_fn, \
    "an unplaceable point must ask him for the ward"
assert "_finish_pin_registration(" not in ask_fn, "the ask step must not register"

confirm_branch = src.split('elif state == "pin.ward_confirm":')[1].split(
    'elif state == "pin.ward":')[0]
assert "_finish_pin_registration(" in confirm_branch, \
    "his confirmation is what registers the point"
assert 'conversation.set_state(phone, "pin.ward", data)' in confirm_branch, \
    "a 'not right' answer must ask for the ward by name"

ward_branch = src.split('elif state == "pin.ward":')[1].split("elif state ==")[0]
assert "_finish_pin_registration(" in ward_branch, \
    "the ward he names must register the point with THAT ward"
assert "set_ward(" not in ward_branch, "no update-after-the-fact: we register with his ward"

reg_fn = src.split("def _finish_pin_registration(")[1].split("def ")[0]
assert "conversation.clear_state(phone)" in reg_fn
assert "wards.context_for(ward) == wards.PERI_URBAN" in src, \
    "peri-urban wards (Isiolo town) must take the different branch"
assert "ward ya {ward}" in src or "ward {ward}" in src, \
    "the receipt must state the ward he agreed to"
# The back button works from both ward states: no dead ends inside the flow.
assert 'if state in ("pin.ward_confirm", "pin.ward"):' in src, \
    "back/rudi must work from the ward steps"
worker = io.open("scripts/_run_tests_in_container.sh", encoding="utf-8").read()
assert "test_wards" in worker, "this suite belongs in the battery"
print("pin flow: derives the ward, asks HIM, registers only on confirmation")

print("\nward lookup tests OK")
