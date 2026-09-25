"""Pest & parasite windows: thresholds, tiers, wording, and the check-first boundary.

The boundary block is the important one: it asserts mechanically that nothing we
render for a herder names a drug, recommends a treatment, or diagnoses anything.
That is the design constraint this feature exists to respect, so it gets a test
rather than a comment.

Pure module: runs anywhere, no DB, no network (the DB path lives in the app).
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.services import pests  # noqa: E402

# --- 1) input maths ---------------------------------------------------------
inp = pests.build_inputs([1, 0, 0, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 5])
assert inp.rain_7d_mm == 5.0, inp
assert inp.rain_14d_mm == 9.0, inp
assert inp.days_since_wet == 0, inp
assert inp.wet_days_streak == 1, inp
empty = pests.build_inputs([])
assert empty.rain_7d_mm == 0.0 and empty.days_since_wet is None, empty
print("inputs: rain windows, days-since-wet and streak OK")

# --- 2) a dry season raises nothing ----------------------------------------
dry = pests.build_inputs([0.0] * 30, soil_moisture=0.10, temp_max_c=34.0,
                         humidity=25.0)
o = pests.outlook(dry)
assert o.highest_tier == pests.TIER_QUIET, o
assert o.top is None and not o.active, o
assert "ya kawaida" in pests.message(o, "swa")
assert pests.weekly_line(o, "swa") is None, "quiet weeks must stay silent"
print("dry season: no window, no weekly line OK")

# --- 3) rain onset raises ticks and worms ----------------------------------
onset = [0.0] * 16 + [4.0, 6.0, 5.0, 8.0, 0.0, 0.0, 3.0]
wet = pests.build_inputs(onset, soil_moisture=0.22, temp_max_c=24.0,
                         humidity=70.0, ndmi=0.05)
o = pests.outlook(wet)
tick = next(w for w in o.windows if w.key == "ticks")
worm = next(w for w in o.windows if w.key == "worms")
assert tick.tier == pests.TIER_HIGH, tick
assert worm.tier in (pests.TIER_RISING, pests.TIER_HIGH), worm
assert tick.reason_lines("swa") and "mm" in tick.reason_lines("swa")[0], tick
line = pests.weekly_line(o, "swa")
assert line and "Kuwa makini" in line, line
msg = pests.message(o, "swa")
assert "Angalia mifugo yako" in msg, msg
assert "•" in msg, msg
assert "Umeona kupe" in msg or "Umekagua kope" in msg, msg
assert "afisa wa mifugo" in msg, msg
print("rain onset: tick + worm windows raised, message quotes the numbers OK")

# --- 4) one available signal can never raise a window past 'watch' ---------
solo = pests.PestInputs(rain_7d_mm=50.0, rain_14d_mm=50.0)
solo_out = pests.outlook(solo)
assert all(w.tier in (pests.TIER_QUIET, pests.TIER_WATCH)
           for w in solo_out.windows), solo_out
assert next(w for w in solo_out.windows if w.key == "ticks").tier == \
    pests.TIER_WATCH, solo_out
print("conservatism: a single signal cannot fire an alert OK")

# --- 5) drizzle is not rain: nothing fires --------------------------------
light = pests.build_inputs([0.4] * 20, soil_moisture=0.08, temp_max_c=22.0,
                           humidity=45.0)
assert pests.outlook(light).highest_tier == pests.TIER_QUIET, "0.4 mm is not a wet day"
print("drizzle below the wet-day threshold: nothing fires OK")

# --- 6) wet ground raises the hoof window ---------------------------------
hoof = pests.build_inputs([0, 0, 0, 2, 3, 4, 2], soil_moisture=0.25,
                          temp_max_c=20.0, humidity=55.0)
h = next(w for w in pests.outlook(hoof).windows if w.key == "hooves")
assert h.tier == pests.TIER_HIGH, h
assert any("mfululizo" in r for r in h.reason_lines("swa")), h
print("consecutive wet days: hoof window raised OK")

# --- 7) the check-first boundary, asserted over everything we render -------
samples = [pests.message(o, "swa"), pests.message(o, "eng"),
           pests.message(solo_out, "swa"), pests.message(solo_out, "eng"),
           pests.quiet_sentence("swa"), pests.quiet_sentence("eng"),
           pests.weekly_line(o, "swa"), pests.weekly_line(o, "eng")]
for win in o.windows:
    samples.append(pests.observation_question(win, "swa"))
    samples.append(pests.observation_question(win, "eng"))
for text in samples:
    low = (text or "").lower()
    for word in pests.FORBIDDEN_WORDS:
        assert word not in low, f"{word!r} in {text!r}"
    # No diagnosis either: we describe conditions, never the animal's state.
    assert "ugonjwa" not in low, text
    assert "ana minyoo" not in low and "ana kupe" not in low, text
print("boundary: no drug names, no treatment verbs, no diagnosis OK")

# --- 8) the guidance file is present and reviewable -----------------------
g = pests.load_guidance()
assert g, "pest_guidance.yaml did not load"
assert g.get("status") == "needs_review", "the file must say it needs a vet's review"
for key in pests.PEST_KEYS:
    entry = pests.pest_guidance(key, "swa")
    assert entry["label"] and entry["where_to_look"] and entry["if_found"], entry
    assert entry["where_to_look"] != pests.pest_guidance(key, "eng")["where_to_look"]
assert "afisa wa mifugo" in pests.pest_guidance("ticks", "swa")["if_found"]
print("guidance: four pests, both languages, routed to the vet OK")

# --- 9) the one-tap answer ------------------------------------------------
assert pests.observation_for_digit("1") is True
assert pests.observation_for_digit(" 2 ") is False
assert pests.observation_for_digit("12") is None, "a herd size is not an answer"
assert pests.observation_for_digit("hapana") is None
assert "hapana" in pests.thanks_for_observation(False, "swa")
assert "Asante" in pests.thanks_for_observation(True, "swa")
print("observation digits: 1/2 only, other numbers ignored OK")

# --- 10) units survive the voice pass -------------------------------------
from app.services.speech import speech_text  # noqa: E402

spoken = speech_text(tick.reason_lines("swa")[0], "swahili")
assert "milimita" in spoken.lower(), spoken
assert re.search(r"\d+\s*mm", spoken) is None, spoken
print("voice: '22 mm' is spoken as milimita OK")

print("\nPEST WINDOWS OK")

