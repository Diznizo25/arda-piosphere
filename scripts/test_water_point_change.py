"""Behavioural test: a herder can CHANGE his water point (and tap water exists).

What this pins, all of it asked for by herders:

  1. Tap / piped water is a real water type. In town a herder buys from a tap or a
     tank; before this the pin flow offered only borehole/well/river, and "tap"
     existed nowhere but as a map label.
  2. "I moved my animals" is answered with a SWITCH, not a dead end. The old flow
     refused any pin within 300 m of a known point with "already registered".
  3. A name/type/ward can be corrected in place ('BADILISHA'), because a duplicate
     point splits the status reports and the rasters.
  4. Coordinates are NOT editable: moving a point is a re-pin, since the rings and
     the precomputed rasters must move with it.

Drives the real handlers with stubbed IO, same style as test_pin_flow.py.

    python scripts/test_water_point_change.py
"""
from __future__ import annotations

import inspect
import sys

sys.path.insert(0, ".")

from app.routers import whatsapp as wa  # noqa: E402
from app.services import water_sources, water_validation  # noqa: E402

SENT: list[tuple] = []
STATE: dict = {}
SWITCHED: list[str] = []
UPDATED: list[dict] = []

PHONE = "+254700000001"


class Point:
    def __init__(self, pid: str, name: str, wtype: str, ward: str = "Oldonyiro"):
        self.id, self.name, self.water_type, self.ward = pid, name, wtype, ward
        self.label = name or ward


POINTS = {
    "w-old": Point("w-old", "Kisima cha Oldonyiro", "borehole"),
    "w-new": Point("w-new", "Kisima cha Kipsing", "borehole"),
    "w-tap": Point("w-tap", "Mfereji wa Bulla Pesa", "tap", "Bulla Pesa"),
}


def _update(wid, *, name=None, water_type=None, ward=None):
    UPDATED.append({"id": wid, "name": name, "water_type": water_type, "ward": ward})
    p = POINTS[wid]
    if name:
        p.name, p.label = name, name
    if water_type:
        p.water_type = water_type
    if ward:
        p.ward = ward
    return p


# --- stubbed IO ---------------------------------------------------------------
wa.get_last_location = lambda phone: (37.2402, 0.5669)          # Lengwenyi area
wa.get_water_source = lambda phone: {"id": "w-old", "name": "Kisima cha Oldonyiro",
                                     "ward": "Oldonyiro", "lon": 37.2402, "lat": 0.5669}
wa.water_reach.list_nearby_water_sources = lambda lon, lat, limit=10: [
    {"water_source_id": "w-old", "name": "Kisima cha Oldonyiro", "water_type": "borehole",
     "ward": "Oldonyiro", "distance_km": 0.2, "direction_swa": "kaskazini"},
    {"water_source_id": "w-new", "name": "Kisima cha Kipsing", "water_type": "borehole",
     "ward": "Oldonyiro", "distance_km": 3.4, "direction_swa": "mashariki"},
    {"water_source_id": "w-tap", "name": "Mfereji wa Bulla Pesa", "water_type": "tap",
     "ward": "Bulla Pesa", "distance_km": 5.0, "direction_swa": "kaskazini"},
]
wa.water_sources.get_water_source = lambda wid: POINTS.get(wid)
wa.water_sources.update_water_source = _update
wa.set_water_source = lambda phone, wid: SWITCHED.append(wid)
wa.conversation.clear_state = lambda phone: STATE.clear()
wa.conversation.set_state = lambda phone, state, data=None: STATE.update(
    {"state": state, "data": data or {}})
wa.whatsapp_client.send_text = lambda phone, text, **kw: SENT.append(("text", text))
wa.whatsapp_client.send_quick_reply_buttons = lambda phone, text, buttons, **kw: (
    SENT.append(("buttons", text, buttons)))
wa._send_reply = lambda phone, p, text, **kw: SENT.append(("reply", text))
wa._handle_map_request = lambda phone, p: SENT.append(("map", ""))
wa._hint = lambda lang: ""


class Pastoralist:
    preferred_language = "swahili"
    voice_replies = False
    water_source_id = "w-old"


p = Pastoralist()


def _texts() -> list[str]:
    return [s[1] for s in SENT if s[0] in ("text", "reply")]


# --- 1) tap / tank are real types, selectable by word or button ---------------
for key in ("tap", "tank"):
    assert key in water_validation.WATER_TYPES, key
assert wa._parse_water_type("mfereji") == "tap"
assert wa._parse_water_type("maji ya bomba") == "tap"
assert wa._parse_water_type("type:tank") == "tank"
assert wa._parse_water_type("Kisima cha kuchimba") == "well"
assert wa._parse_water_type("hakuna kitu") is None
for lang in ("swahili", "english"):
    for key in water_validation.WATER_TYPES:
        assert len(wa._type_button_title(key, lang)) <= 20, (lang, key)
assert water_validation.PERI_URBAN_TYPE_BUTTONS[0] == "tap", \
    "in town the first honest one-tap choice is a tap"
print("types: tap + tank selectable, button titles fit WhatsApp OK")

# --- 2) 'BADILISHA' shows his point + nearby alternatives, never his own ------
STATE.clear()
SENT.clear()
wa._handle_change_request(PHONE, p)
assert STATE["state"] == "change.choose", STATE
assert STATE["data"]["nearby"] == ["w-new", "w-tap"], STATE["data"]
assert STATE["data"]["current"] == "w-old"
body = SENT[-1][1]
assert "Kisima cha Oldonyiro" in body, "he must see which point he has now"
assert "1. Kisima cha Kipsing" in body and "2. Mfereji wa Bulla Pesa" in body, body
assert "JINA" in body and "PIN" in body, body
print("change: current point + numbered alternatives + name/type/PIN options OK")

# --- 3) tapping a number SWITCHES the point (and shows the map) --------------
SWITCHED.clear()
SENT.clear()
wa._handle_change_step(PHONE, p, "change.choose", STATE["data"], "2")
assert SWITCHED == ["w-tap"], SWITCHED
assert p.water_source_id == "w-tap", p.water_source_id
assert any("Mfereji wa Bulla Pesa" in t for t in _texts()), _texts()
assert ("map", "") in SENT, "the map is the proof he can check"
print("change: number -> switched to that point, confirmation + map OK")

# --- 4) renaming the point writes a NAME, not a new point --------------------
p.water_source_id = "w-old"
UPDATED.clear()
SENT.clear()
STATE.clear()
wa._handle_change_request(PHONE, p)
wa._handle_change_step(PHONE, p, "change.choose", STATE["data"], "jina")
assert STATE["state"] == "change.name", STATE
wa._handle_change_step(PHONE, p, "change.name", STATE["data"], "kisima cha kipsing")
assert UPDATED and UPDATED[-1]["name"] == "Kisima Cha Kipsing", UPDATED
assert UPDATED[-1]["water_type"] is None, "a rename must not touch the type"
assert any("Kisima Cha Kipsing" in t for t in _texts()), _texts()
print("change: rename updates the name in place OK")

# --- 5) a bare type word is the ANSWER (no extra question) -------------------
for word, expected in (("mfereji", "tap"), ("tanki", "tank")):
    UPDATED.clear()
    SENT.clear()
    STATE.clear()
    wa._handle_change_request(PHONE, p)
    wa._handle_change_step(PHONE, p, "change.choose", STATE["data"], word)
    assert UPDATED and UPDATED[-1]["water_type"] == expected, (word, UPDATED)
print("change: 'mfereji' / 'tanki' typed at the menu are applied directly OK")

# --- 6) 'aina' asks, then the answer is applied ------------------------------
UPDATED.clear()
STATE.clear()
wa._handle_change_request(PHONE, p)
wa._handle_change_step(PHONE, p, "change.choose", STATE["data"], "aina")
assert STATE["state"] == "change.type", STATE
wa._handle_change_step(PHONE, p, "change.type", STATE["data"], "chemchemi")
assert UPDATED[-1]["water_type"] == "spring", UPDATED
print("change: 'aina' -> question -> applied OK")



# --- 7) a duplicate PIN is an OFFER TO SWITCH, not a refusal ------------------
DUPE_NOT_HIS = type("R", (), {
    "is_duplicate": True, "duplicate_water_source_id": "w-new", "nearest_source_id": "w-new",
    "has_nearby_source": True, "distance_to_nearest_m": 40.0})()
wa.water_validation.validate_pin = lambda lon, lat: DUPE_NOT_HIS
p.water_source_id = "w-old"
STATE.clear()
SENT.clear()
wa._handle_pin_request(PHONE, p)
assert STATE["state"] == "change.switch_confirm", STATE
assert SENT[-1][0] == "buttons", SENT[-1]
assert [b[0] for b in SENT[-1][2]] == ["act:switch", "act:new"], SENT[-1]
assert "Kisima cha Kipsing" in SENT[-1][1], SENT[-1]
SWITCHED.clear()
wa._handle_change_step(PHONE, p, "change.switch_confirm", STATE["data"], "act:switch")
assert SWITCHED == ["w-new"], SWITCHED
print("pin on a known point: one-tap switch offered and applied OK")

# --- 8) ... but a pin on HIS OWN point still says 'already yours' ------------
STATE.clear()
SENT.clear()
wa._handle_pin_request(PHONE, p)          # p is now on w-new, the duplicate
assert "state" not in STATE, STATE
assert SENT[-1][0] == "text" and "tayari" in SENT[-1][1], SENT[-1]
print("pin on his own point: tells him it is already registered OK")

# --- 9) coordinates are not editable anywhere -------------------------------
assert set(water_sources._EDITABLE_COLUMNS) == {"name", "water_type", "ward"}
print("edit surface: name / type / ward only, coordinates untouched OK")

# --- 10) the menu now says 'register OR change' ------------------------------
row = [r for r in wa.MENU_SECTIONS[1][1] if r[0] == "svc:pin"][0]
row_en = [r for r in wa.MENU_SECTIONS_EN[1][1] if r[0] == "svc:pin"][0]
assert "badilisha" in row[2].lower(), row
assert "change" in row_en[2].lower(), row_en
assert len(row[1]) <= 24 and len(row[2]) <= 72, row
assert len(row_en[1]) <= 24 and len(row_en[2]) <= 72, row_en
print("menu: 'Chanzo cha maji / Water point' covers register + change OK")

# --- 11) 'badilisha chanzo' is matched BEFORE the water keywords -------------
src = inspect.getsource(wa._handle_text)
assert src.index("CHANGE_KEYWORDS") < src.index("WATER_KEYWORDS"), \
    "a change request must not be answered as a water-status question"
assert "change." in src or "_handle_change_request" in src, src
print("routing: change keywords checked before the water branch OK")

print("\nWATER POINT CHANGE OK")

