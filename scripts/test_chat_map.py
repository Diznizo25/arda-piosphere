"""The AI reads the MAP'S NUMBERS (not its pixels) -- grounded, cached, honest.

"Can the AI read the map?" has two honest answers, and this test pins the first:

  * the NUMBERS: the same raster the /mapview page paints (usable / dry / bare /
    unreadable shares) now reach the chat layer, so a reply can quote them. Cached,
    because re-reading a COG to answer a question is pure latency.
  * the PIXELS: a vision model looking at the rendered PNG is a separate route; it is
    not claimed here and must not be faked by inventing a percentage.

The hard part is not the number, it is the honesty: a grey patch means "we could not
read it", NOT "nothing grows there". With nothing readable at all there is no
sentence to say -- and this test insists on that silence.

    python scripts/test_chat_map.py
"""
from __future__ import annotations

import sys
import types

sys.path.insert(0, ".")

import app.services as services_pkg  # noqa: E402
from app.services import chat  # noqa: E402

calls = {"n": 0}


def pasture_overlay_status(ws_id, species, interval):
    calls["n"] += 1
    return {"available": True, "usable_pct": 12, "blank_pct": 31,
            "frac": {"green": 5, "dry": 7, "bare": 88},
            "bounds": [[36.9, 0.1], [37.2, 0.4]]}


fake = types.ModuleType("app.services.map_renderer")
fake.pasture_overlay_status = pasture_overlay_status
services_pkg.map_renderer = fake


class Herder:
    phone_number = "254700000001"
    preferred_language = "swahili"
    primary_species = "cattle"
    water_source_id = "w1"
    water_interval = "daily"


chat._MAP_CACHE.clear()
facts = chat._map_facts(Herder())
m = facts["map"]
assert m["usable_pct"] == 12, m
assert m["bare_pct"] == 88 and m["blank_pct"] == 31, m
assert m["readable"] is True
assert "kijani" in m["what_it_is"] and "kijivu" in m["what_it_is"], m["what_it_is"]
assert "12" in m["suggested_reply"] and "31" in m["suggested_reply"], m
assert "satellite" in m["suggested_reply"], m
assert calls["n"] == 1, calls
print("map facts: usable/bare/unreadable shares reach the chat layer OK")

again = chat._map_facts(Herder())
assert calls["n"] == 1, f"a second question re-read the raster ({calls['n']} reads)"
assert again["map"]["usable_pct"] == 12
print("cache: the same point is not re-read for every question OK")

allowed = chat.allowed_numbers(facts, "malisho yapo wapi?")
for n in ("12", "31", "88", "5", "7", "69"):
    assert n in allowed, (n, sorted(allowed))
assert "69" in m["suggested_reply"], "the readable base must be stated, not implied"
print("validation: every share the model may quote is traceable to the facts OK")

said = chat.deterministic_answer({"map": m}, "swahili")
assert "12" in said and "ardhi tupu" in said.lower(), said


class HerderEn:
    phone_number = "254700000002"
    preferred_language = "english"
    primary_species = "cattle"
    water_source_id = "w1"
    water_interval = "daily"


m_en = chat._map_facts(HerderEn())["map"]
said_en = chat.deterministic_answer({"map": m_en}, "english")
assert "12%" in said_en and "bare" in said_en.lower(), said_en
print("deterministic answer: states the shares in the herder's own language OK")

# --- honesty: unreadable ground is NOT bare ground --------------------------
fake.pasture_overlay_status = lambda *a, **k: {
    "available": False, "usable_pct": None, "blank_pct": 100, "frac": None}
chat._MAP_CACHE.clear()
unreadable = chat._map_facts(Herder())["map"]
assert unreadable["blank_pct"] == 100
assert unreadable["readable"] is False
assert "suggested_reply" not in unreadable, \
    "with nothing readable there is no share to claim -- stay silent, not 'bare'"
print("honesty: 100% unreadable produces no claim at all OK")


# --- a FAILED read is retried, never cached for the whole TTL ---------------
def _boom(*a, **k):
    calls["n"] += 1
    raise RuntimeError("raster missing")


fake.pasture_overlay_status = _boom
chat._MAP_CACHE.clear()
assert chat._map_facts(Herder()) == {}
before = calls["n"]
chat._map_facts(Herder())
assert calls["n"] == before + 1, "a failed raster read must not be cached"
print("failure: a broken COG costs one answer, not thirty minutes OK")

# --- routing: a pasture question is what carries the map facts --------------
assert "pasture" in chat.intent_sections("malisho yapo wapi?"), \
    chat.intent_sections("malisho yapo wapi?")
assert "pasture" not in chat.intent_sections("bei ya ng'ombe ni ngapi?"), \
    "a market question must not drag the map into the prompt"
print("routing: only pasture questions carry the map facts OK")

print("\nCHAT MAP GROUNDING OK")
