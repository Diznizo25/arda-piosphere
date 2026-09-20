"""Landmark-intake tests: can the system understand a place named in free text?

Pure — no DB, no network. Water-point names are stubbed so the tests cover the
scoring logic rather than whatever rows happen to be in the database today.

Run: python scripts/test_landmarks.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.services import landmarks as lm  # noqa: E402

# --- 1) normalisation / tokenisation ----------------------------------------
assert lm.normalise("Niko karibu na Oldonyo Sabor!") == "niko karibu na oldonyo sabor"
assert lm.normalise("Wamba  Market,") == "wamba market"
assert lm.tokens("niko karibu na Oldonyo Sabor") == ["oldonyo", "sabor"]
# Kind words ("market", "mto", "soko") are deliberately KEPT: they are extra message
# tokens (harmless for subset matching) and they discriminate between a town and the
# market inside it.
assert lm.tokens("I am near Wamba market") == ["wamba", "market"]
assert lm.tokens("nipo") == []          # a location word alone names no place
print("normalisation + stopwords OK")

# --- 2) the gazetteer is real and loaded ------------------------------------
marks = lm.load_gazetteer()
assert len(marks) > 100, f"expected the built gazetteer, got {len(marks)}"
assert any(m.name == "Wamba" for m in marks), "Wamba should be in the gazetteer"
kinds = {m.kind for m in marks}
assert {"river", "village", "peak"} <= kinds, kinds
print(f"gazetteer OK ({len(marks)} named places)")

# --- 3) scoring ladder -------------------------------------------------------
TOK = lm.tokens
assert lm.score_name(TOK("Lengwenyi"), "Lengwenyi", set(), "village") == 1.0
assert lm.score_name(TOK("niko karibu na Lengwenyi"), "Lengwenyi", set(), "village") >= 0.97
assert lm.score_name(TOK("nipo Lengwenyi leo"), "Lengwenyi", set(), "village") >= 0.97
# Spellings vary: missing/extra letters must still match.
assert lm.score_name(TOK("niko Oldonyo Sabo"), "Oldonyo Sabor", set(), "village") >= 0.74
# The kind word nudges the right KIND of landmark above the others.
river_hint = lm.score_name(TOK("mto Ewaso"), "Ewaso", {"river"}, "river")
river_plain = lm.score_name(TOK("mto Ewaso"), "Ewaso", set(), "river")
assert river_hint > river_plain
# Unrelated words score low: this is what stops us inventing a location.
assert lm.score_name(TOK("habari yako leo"), "Wamba", set(), "village") < 0.6
assert lm.score_name(TOK("mifugo yangu ni wengi"), "Isiolo", set(), "town") < 0.6
print("scoring ladder OK")

# --- 4) a herder with no location words at all is left alone -----------------
assert lm.resolve("habari yako", include_water_points=False).status == "none"
assert lm.resolve("ng'ombe wangu wana njaa", include_water_points=False).status == "none"
res = lm.resolve("nipo", include_water_points=False)
assert res.status == "none" and res.reason == "no_name_words"
print("no false positives on ordinary chatter OK")

# --- 5) a real gazetteer place resolves, and ambiguity is REPORTED -----------
res = lm.resolve("niko karibu na Wamba", include_water_points=False)
assert res.matched, res
assert res.best.name.lower().startswith("wamba"), res.best
assert -1.0 <= res.best.lat <= 2.0, res.best  # Kenya latitudes
print(f"resolved landmark: {res.best.name} ({res.best.kind}) -> "
      f"{res.best.lat:.3f},{res.best.lon:.3f}")

# Two far-apart places that both match must come back as candidates, NOT a guess.
far = [
    lm.Landmark("Lengo", 0.60, 37.20, "village", score=0.95),
    lm.Landmark("Lengo", 0.20, 38.10, "village", score=0.94),
]
assert len(lm._distinct_places(far, far[0])) == 1
# ...but names that are the same physical place are not "ambiguous".
same = [
    lm.Landmark("Wamba", 0.4600, 37.3000, "town", score=0.95),
    lm.Landmark("Wamba market", 0.4610, 37.3010, "market", score=0.94),
]
assert lm._distinct_places(same, same[0]) == []
print("ambiguity detection OK")

# --- 5b) DUPLICATE NAMES are the normal case, not an edge case ---------------
# Measured on our own gazetteer: 194 names appear in two or more places far apart,
# because a river's name repeats along its whole course. An exact match therefore
# proves nothing about WHICH place, and this is the bug that probing found:
# "ewaso nyiro" scored 1.00 and was returned as a confident match.
res = lm.resolve("ewaso nyiro", include_water_points=False)
assert res.status == "ambiguous", (res.status, res.reason)
assert res.best is None, "we must not hand back a confident best guess here"
assert len(res.candidates) >= 2, res.candidates
assert all(c.dist_km is None for c in res.candidates), \
    "without a reference location, distances are meaningless and must not be shown"
# With a location we already know, the nearest stretch of that river wins - we do
# not interrogate a herder about something we can work out ourselves.
near_wamba = (0.4600, 37.3000)
res2 = lm.resolve("ewaso nyiro", include_water_points=False, near=near_wamba)
assert res2.matched and res2.reason == "nearest_to_known_location", res2
assert res2.best.dist_km is not None
# A unique name stays matched, with or without context.
res3 = lm.resolve("nipo Wamba", include_water_points=False, near=near_wamba)
assert res3.matched and res3.best.name.lower().startswith("wamba"), res3
# A duplicate name with no candidate anywhere near the herder must still ask.
res4 = lm.resolve("ewaso nyiro", include_water_points=False, near=(-1.30, 36.80))
assert res4.status == "ambiguous" and "far_from_known" in res4.reason, res4
print("duplicate-name handling OK")

# --- 6) the water-point name space is searched too --------------------------
wat = [lm.Landmark("Lengwenyi well", 0.5669, 37.2402, "well", source="water_point",
                   water_source_id="ws-1")]
real_loader = lm.water_point_landmarks
lm.water_point_landmarks = lambda: wat          # stub the DB
lm.load_gazetteer.cache_clear()                 # not strictly needed, clarity
res = lm.resolve("nipo Lengwenyi")
assert res.matched, res
assert res.best.water_source_id == "ws-1", res.best
assert res.best.source == "water_point"
res2 = lm.resolve("niko karibu na Lengwenyi well")
assert res2.matched and res2.best.water_source_id == "ws-1", res2
lm.water_point_landmarks = real_loader
print("water-point names resolve OK")

# --- 7) answering "which one?" in the herder's language ---------------------
listing = lm.describe(same + far[1:], "swahili")
assert "Ni yupi?" in listing and "1. Wamba" in listing, listing
assert "kijiji" in listing, listing            # kind shown in Swahili
eng = lm.describe(same, "english")
assert "Which one?" in eng and "town" in eng, eng
# --- 7b) unknown place names are admitted, not guessed ----------------------
# "niko karibu na mahali ambao hatujui" must not be answered with a menu or a guess.
assert lm.looks_like_place_statement("niko karibu na Oldonyo Sabor")
assert lm.looks_like_place_statement("nipo kijiji fulani")
assert lm.looks_like_place_statement("I am near Somewhere")
# ...but never hijack a real question: these must fall through to the other services.
assert not lm.looks_like_place_statement("niko na ng'ombe 40")
assert not lm.looks_like_place_statement("niko karibu na maji?")
assert not lm.looks_like_place_statement("niko karibu na malisho mazuri")
assert not lm.looks_like_place_statement("mvua itanyesha lini hapa")
assert not lm.looks_like_place_statement("habari yako")          # does not say where
assert not lm.looks_like_place_statement(
    "niko karibu na kijiji kimoja kikubwa sana cha wachungaji")   # too long to be a place name
reply = lm.unknown_place_reply("niko karibu na Nowhere", "swahili")
assert "sijui eneo hilo" in reply and "location" in reply, reply
assert "do not know that place" in lm.unknown_place_reply("I am near Nowhere", "english")
print("unknown-place handling OK")

# --- 7c) an unhelpful list is replaced by a request for a location -----------
river = [lm.Landmark("Ewaso Nyiro", 0.70, 37.23, "river", score=1.0),
         lm.Landmark("Ewaso Nyiro", 0.66, 36.99, "river", score=1.0),
         lm.Landmark("Ewaso Nyiro", 0.58, 37.48, "river", score=1.0)]
collapsed = lm.describe(river, "swahili")
assert "Tuma eneo lako" in collapsed and "1." not in collapsed, collapsed
# Two different KINDS with the same name do stay a list (the kind distinguishes them).
mixed = [lm.Landmark("Kamanga", 0.76, 37.90, "peak", score=1.0),
         lm.Landmark("Kamanga", -0.32, 38.24, "river", score=1.0)]
mixed[1].dist_km = 40.0
as_list = lm.describe(mixed, "swahili")
assert "1. Kamanga (mlima)" in as_list and "2. Kamanga (mto" in as_list, as_list
print("ambiguity prompt OK")

print("landmark tests OK")

# --- 8) wiring: the WhatsApp path must use the landmark intake correctly ------
# Pure source checks, because these are the failure modes that would otherwise only
# show up as a herder getting the wrong reply in the field.
import io  # noqa: E402

wa = io.open("app/routers/whatsapp.py", encoding="utf-8").read()

assert "_resolve_message_place(" in wa, "the landmark intake must be wired in"
assert "_deliver_location_info(" in wa
assert "landmark.confirm" in wa and "_handle_landmark_confirm(" in wa, \
    "an ambiguous name must ask which one, and accept a numbered answer"

# ONE code path for "everything about this place": a location pin and a named
# landmark must produce the same reply, or they will drift apart.
loc_fn = wa.split("def _handle_location(")[1].split("def ")[0]
assert "_deliver_location_info(" in loc_fn, \
    "a WhatsApp location pin must go through the same delivery path as a landmark"
assert "get_advisory(" not in loc_fn, "the advisory must not be duplicated in _handle_location"

# ORDERING (a bug we actually hit): the landmark hook must run AFTER the guided
# flows. During the PIN flow the herder types a place NAME for a new water point,
# and hijacking that would break registration.
landmark_pos = wa.index("place, place_handled = _resolve_message_place(")
flow_pos = wa.index("if _handle_active_flow(phone, pastoralist, text):")
onboard_pos = wa.index("if not pastoralist.is_onboarded:")
assert flow_pos < landmark_pos, "guided flows must get the message before landmark intake"
assert onboard_pos < landmark_pos, "onboarding must get the message before landmark intake"
# ...and BEFORE the service keywords, so the place is remembered for what follows.
assert landmark_pos < wa.index('if any(k in text_lower for k in WATER_KEYWORDS)'), \
    "the place must be resolved before the service dispatch uses their location"

# A named place with nothing else asked must produce the full picture, not a menu.
assert "if place is not None:" in wa, "a bare place name must deliver the full info"
tail = wa.split("if place is not None:")[1]
assert "_deliver_location_info(" in tail[:400], tail[:200]

# The menu must teach the capability, or nobody will use it.
assert "niko karibu na" in wa and "I am near" in wa, "the menu should show the landmark option"
print("whatsapp landmark wiring + ordering OK")

