"""Behavioural test: the pin flow confirms the ward with the pastoralist BEFORE it writes.

Drives the real state handlers with stubbed IO — same style as test_flow_exit.py, so it
runs in the container (importing the router pulls in the advisery stack).

    python scripts/test_pin_flow.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.routers import whatsapp as wa  # noqa: E402

CREATED: list[dict] = []
SENT: list[tuple] = []
STATE: dict = {}


def _fake_validate(lon, lat):
    return type("R", (), {"is_duplicate": False, "has_nearby_source": False,
                          "distance_to_nearest_m": None})()


wa.get_last_location = lambda phone: (37.2402, 0.5669)      # Lengwenyi area, Oldonyiro
wa.water_validation.validate_pin = _fake_validate
wa.water_sources.create_water_source = lambda **kw: (
    CREATED.append(kw) or type("WS", (), {"id": "ws-test"})())
wa.build_tracker.start_build = lambda *a, **k: None
wa.set_water_source = lambda *a, **k: None
wa.conversation.clear_state = lambda phone: STATE.clear()
wa.conversation.set_state = lambda phone, state, data=None: STATE.update(
    {"state": state, "data": data or {}})
wa.whatsapp_client.send_text = lambda phone, text, **kw: SENT.append(("text", text))
wa.whatsapp_client.send_quick_reply_buttons = lambda phone, text, buttons, **kw: (
    SENT.append(("buttons", text, buttons)))
wa._send_reply = lambda phone, p, text, **kw: SENT.append(("reply", text))


class Pastoralist:
    preferred_language = "swahili"
    voice_replies = False
    water_source_id = None


p = Pastoralist()
PHONE = "+254700000001"

# --- 1) the name step ASKS about the ward and writes nothing ------------------
wa._start_pin_ward_check(PHONE, p, "well", "Kisima cha Kipsing")
assert not CREATED, f"nothing may be registered before he confirms: {CREATED}"
assert STATE["state"] == "pin.ward_confirm", STATE
assert SENT[-1][0] == "buttons", f"one tap to confirm, not a typed word: {SENT[-1]}"
assert "Oldonyiro" in SENT[-1][1], SENT[-1]
assert "Kisima cha Kipsing" in SENT[-1][1], "he should recognise his own name: %s" % SENT[-1]
print("  name -> ward question, nothing written: OK")

# --- 2) 'ndiyo' registers the point under the ward he confirmed ---------------
data = STATE["data"]
wa._handle_pin_step(PHONE, p, "pin.ward_confirm", data, "Ndiyo")
assert len(CREATED) == 1, CREATED
assert CREATED[0]["ward"] == "Oldonyiro", CREATED
assert CREATED[0]["name"] == "Kisima cha Kipsing", CREATED
assert CREATED[0]["water_type"] == "well", CREATED
assert "Oldonyiro" in SENT[-1][1], "the receipt must state the ward he agreed to"
print("  'ndiyo' -> registered under Oldonyiro, receipt states it: OK")

# --- 3) 'si sahihi' asks for the ward, and only then registers ----------------
CREATED.clear()
SENT.clear()
STATE.clear()
wa._start_pin_ward_check(PHONE, p, "well", "Kisima cha Kipsing")
data = STATE["data"]
wa._handle_pin_step(PHONE, p, "pin.ward_confirm", data, "Si sahihi")
assert not CREATED, "a 'not right' answer must not register anything"
assert STATE["state"] == "pin.ward", STATE
assert "ward" in SENT[-1][1].lower(), SENT[-1]
wa._handle_pin_step(PHONE, p, "pin.ward", data, "burat")
assert len(CREATED) == 1 and CREATED[0]["ward"] == "Burat", CREATED
print("  'si sahihi' -> asks, then registers under the ward he typed: OK")

# --- 4) a ward we do not serve registers nothing ------------------------------
CREATED.clear()
STATE.clear()
wa._start_pin_ward_check(PHONE, p, "well", None)
data = STATE["data"]
wa._handle_pin_step(PHONE, p, "pin.ward", data, "nairobi")
assert not CREATED, f"an invented ward must register nothing: {CREATED}"
print("  an out-of-county ward registers nothing: OK")

# --- 5) a name is optional; a peri-urban point asks where he GRAZES -----------
CREATED.clear()
SENT.clear()
STATE.clear()
wa.get_last_location = lambda phone: (37.5820, 0.3540)      # Isiolo town
wa._start_pin_ward_check(PHONE, p, "borehole", None)
assert STATE["state"] == "pin.ward_confirm", STATE
assert "Bulla Pesa" in SENT[-1][1], SENT[-1]
data = STATE["data"]
wa._handle_pin_step(PHONE, p, "pin.ward_confirm", data, "Ndiyo")
assert CREATED and CREATED[0]["ward"] == "Bulla Pesa", CREATED
assert "malisho" in SENT[-1][1], "peri-urban: ask where he takes them to graze"
print("  peri-urban: Bulla Pesa + the grazing question: OK")

# --- 6) a point we cannot place asks him, and never guesses ------------------
CREATED.clear()
SENT.clear()
STATE.clear()
wa.get_last_location = lambda phone: (36.8219, -1.2921)      # Nairobi
wa._start_pin_ward_check(PHONE, p, "well", "Kisima")
assert not CREATED, "an unplaceable point must not be registered with an empty ward"
assert STATE["state"] == "pin.ward", STATE
print("  unplaceable point -> asks for the ward, writes nothing: OK")

print("\npin flow: ward confirmed before registration OK")
