"""A herder can leave ANY guided flow at ANY time ('menu'/'cancel'/'rudi').

Covers: onboarding.*, weight.*, pin.* and water-confirm states.
  * 'cancel'/'menu' clears the flow state and shows the services menu
  * 'rudi'/'back' steps BACK one question in weight/pin flows
  * the water-confirm re-list words ('orodha') are NOT swallowed by the exit
"""
from __future__ import annotations

import sys
import types

sys.path.insert(0, ".")

from app.routers import whatsapp  # noqa: E402
from app.services import conversation  # noqa: E402

STATE = {"state": None, "data": {}}
SENT: list[tuple] = []


def _fake_get(phone):
    return STATE["state"], STATE["data"]


def _fake_set(phone, state, data=None):
    STATE["state"] = state
    STATE["data"] = data or {}


def _fake_clear(phone):
    STATE["state"] = None
    STATE["data"] = {}


conversation.get_state = _fake_get
conversation.set_state = _fake_set
conversation.clear_state = _fake_clear
conversation.state_age_seconds = lambda phone: 0


def send_text(to, body):
    SENT.append(("text", body))


def send_buttons(to, body, buttons):
    SENT.append(("buttons", body, buttons))


whatsapp.whatsapp_client.send_text = send_text
whatsapp.whatsapp_client.send_quick_reply_buttons = send_buttons
whatsapp.whatsapp_client.send_image_bytes_url = lambda *a, **k: None
whatsapp.whatsapp_client.send_interactive_list = lambda *a, **k: None
whatsapp._show_menu = lambda phone, p: SENT.append(("menu", "MENU_SENT"))
whatsapp._handle_map_request = lambda *a, **k: SENT.append(("map", "jump"))
whatsapp._start_weight_flow = lambda *a, **k: SENT.append(("weight", "jump"))


def herder(lang="swahili"):
    return types.SimpleNamespace(
        phone_number="+254exit", preferred_language=lang, primary_species="camel",
        voice_replies=False, water_source_id=None, is_onboarded=True, first_name="T",
    )


def run(text, state, data=None):
    STATE["state"] = state
    STATE["data"] = data or {}
    SENT.clear()
    h = herder()
    return whatsapp._handle_active_flow(h.phone_number, h, text)


def assert_cleared(label):
    assert STATE["state"] is None, f"{label}: state not cleared -> {STATE['state']}"
    assert any(s[0] == "menu" for s in SENT), f"{label}: menu not shown"
    print(f"  {label}: OK")


# cancel from every family of flow
for label, state in [("onboarding.name", "onboarding.name"),
                     ("onboarding.count", "onboarding.count"),
                     ("weight.girth", "weight.girth"),
                     ("pin.confirm", "pin.confirm"),
                     ("water-confirm", "onboarding.water")]:
    run("cancel", state)
    assert_cleared(label)

# menu from mid-onboarding
run("menu", "onboarding.language")
assert_cleared("onboarding.language via menu")
run("msaada", "pin.name")
assert_cleared("pin.name via msaada")

# back one step inside weight + pin
run("back", "weight.girth", {"species": "cattle"})
assert STATE["state"] == "weight.species", STATE
print("  weight.girth -> weight.species via back: OK")

run("rudi", "pin.name", {"water_type": "well"})
assert STATE["state"] == "pin.confirm", STATE
print("  pin.name -> pin.confirm via rudi: OK")

# back at the START of a service exits to menu
run("rudi", "weight.species")
assert_cleared("weight.species back -> menu")

# water-confirm re-list words still reach the confirm handler (not swallowed)
called = []
whatsapp._handle_confirm_water_reply = lambda phone, p, t: called.append(t) or True
run("orodha", "onboarding.water")
assert called and called[0] == "orodha", f"orodha swallowed? {called}"
print("  water-confirm 'orodha' -> re-list handler (not swallowed): OK")

# unknown text mid-girth does NOT cancel (keeps flow) but carries the hint
STATE["state"] = "weight.girth"
STATE["data"] = {"species": "cattle"}
SENT.clear()
h = herder()
ok = whatsapp._handle_active_flow(h.phone_number, h, "xyz")
assert ok is True and STATE["state"] == "weight.girth", STATE
assert any("cancel" in b for _, b in SENT), "loop prompt must carry the escape hint"
print("  weight loop prompt carries the escape hint: OK")

print("\nUniversal flow exit/back tests OK.")
