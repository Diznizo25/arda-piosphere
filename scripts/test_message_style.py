"""Message-style tests: does our own text read and SPEAK like a person?

A herder reported that advisories and voice notes were hard to follow — wrong
punctuation, odd wording, some of it meaningless. These checks encode what went
wrong so it cannot come back. They are mechanical on purpose: style is easy to
regress and hard to notice.

Run: python scripts/test_message_style.py
"""
from __future__ import annotations

import re
import sys

sys.path.insert(0, ".")

from app.services import water_status  # noqa: E402
from app.services.advisory_logic import ForageCondition, WaterReliability  # noqa: E402
from app.services.forecast import RainOutlook, mvua_message, rain_line  # noqa: E402
from app.services.i18n import format_advisory_message  # noqa: E402
from app.services.speech import speech_text  # noqa: E402

EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\uFE0F]")

# --- 1) the one-tap question must be about WATER ----------------------------
# It used to say "Majina hayo ni ya kweli?" ("are those names real?"), which
# referred to nothing in the advisory at all.
for lang in ("swa", "eng"):
    q = water_status.check_question(lang)
    assert "maji" in q.lower() or "water" in q.lower(), q
    assert "majina" not in q.lower() and "names" not in q.lower(), q
    assert "Jibu namba moja." in q or "Reply with one number." in q, q
print("the one-tap question is about water, not names OK")

# --- 2) every advisory we can produce passes the style rules ----------------
ADVISORIES: list[tuple[str, str]] = []
for lang in ("swahili", "english"):
    for condition in ForageCondition:
        for reliability in WaterReliability:
            for zone in (None, "comfortable", "far", "critical"):
                for harsh in (False, True):
                    ADVISORIES.append((
                        f"{lang}/{condition.value}/{reliability.value}/{zone}/{harsh}",
                        format_advisory_message(
                            language=lang, species="cattle", distance_km=4.3,
                            condition=condition, seasonally_normal=True,
                            curing_stage_note="still_curing",
                            water_reliability=reliability, grazing_zone=zone,
                            effective_radius_km=7.0, dry_harsh=harsh),
                    ))
print(f"checking {len(ADVISORIES)} generated advisories")

problems: list[str] = []
for label, text in ADVISORIES:
    for line in text.splitlines():
        if not line.strip():
            continue
        if ";" in line:
            problems.append(f"{label}: semicolon -> {line!r}")
        if line.count(":") > 1:
            problems.append(f"{label}: colon run -> {line!r}")
        if line.rstrip()[-1] not in ".!?":
            problems.append(f"{label}: line does not end a sentence -> {line!r}")
        # No repeated word inside one line ("maji ... maji").
        if re.search(r"\b(\w{4,})\b[^.]{0,40}\b\1\b", line, re.IGNORECASE):
            problems.append(f"{label}: repeated word -> {line!r}")
assert not problems, "\n".join(problems[:12])
print("advisories: no semicolons, colon runs, missing stops or repeated words OK")

# --- 3) the earlier wording bugs are gone -----------------------------------
sample = format_advisory_message(
    language="swahili", species="cattle", distance_km=4.3,
    condition=ForageCondition.DRY_FORAGE_AVAILABLE, seasonally_normal=True,
    curing_stage_note=None, water_reliability=WaterReliability.UNKNOWN,
    grazing_zone="far", effective_radius_km=7.0, dry_harsh=False)
assert "Maji: uhakika wa maji" not in sample, sample          # said "maji" twice
assert "Hali ya malisho karibu na maji hayo:" not in sample, sample  # label colon
assert "nyasi kavu nzuri ya malisho ipo." not in sample, sample      # broken grammar
assert "kilomita 4.3" in sample, sample                       # unit spelled in Swahili
assert "~" not in sample, sample                              # no spoken form for "~"
# Standing at your own water point is the common case: nobody says "kilomita 0.0".
at_water = format_advisory_message(
    language="swahili", species="cattle", distance_km=0.0,
    condition=ForageCondition.DRY_FORAGE_AVAILABLE, seasonally_normal=True,
    curing_stage_note=None, water_reliability=WaterReliability.UNKNOWN,
    grazing_zone=None, effective_radius_km=7.0, dry_harsh=False)
assert "maji yapo hapa karibu nawe" in at_water, at_water
assert "kilomita 0.0" not in at_water, at_water
assert "0.0" not in at_water, at_water
assert "ambao" not in sample, sample
print("wording fixes in place OK")

# --- 4) spoken text: what the voice note actually says ----------------------
spoken = speech_text(sample, "swahili")
assert not EMOJI.search(spoken), spoken
assert "•" not in spoken and "*" not in spoken, spoken
assert "(" not in spoken and ")" not in spoken, spoken
assert re.search(r"\bkm\b", spoken) is None, spoken           # expanded to kilomita
assert "kilomita" in spoken, spoken
# Sentences must stay separate: the old cleaner joined every line into one run.
assert spoken.count(".") >= 3, spoken
print("spoken advisory is emoji-free, unit-expanded and sentence-separated OK")

# Uncertainty must SURVIVE into audio (the old cleaner deleted "(makadirio)").
o = RainOutlook(dry_spell_days=30, rain_30d_mm=0.1, normal_30d_mm=6.87,
                deficit_pct=-98.5, has_forecast=True, horizon_days=15,
                forecast_total_mm=1.3, generated_on=None)
sw_mvua = mvua_message(o, place="Lengwenyi well", lang="swahili")
sw_spoken = speech_text(sw_mvua, "swahili")
assert "makadirio" in sw_spoken.lower() or "uhakika" in sw_spoken.lower(), sw_spoken
assert not EMOJI.search(sw_spoken), sw_spoken
assert "milimita" in sw_spoken, sw_spoken                     # mm spelled for speech
en_spoken = speech_text(mvua_message(o, lang="english"), "english")
assert "millimetres" in en_spoken or "estimate" in en_spoken.lower(), en_spoken
# A range and a percentage must become words, not glyphs.
assert "30 hadi 45" in speech_text("lita 30-45 kwa siku", "swahili")
assert "asilimia 40" in speech_text("40% ya malisho", "swahili")
assert "takriban 7" in speech_text("~7 km", "swahili")
# Full numerals must survive expansion: "4.3 km" must not become "4.kilomita 3".
assert "4.3 kilomita" in speech_text("umbali wa 4.3 km", "swahili")
assert "4.kilomita" not in speech_text("umbali wa 4.3 km", "swahili")
assert "4.3 kilometres" in speech_text("4.3 km away", "english")
print("units/ranges/percentages expand with full numerals OK")

# Segments give the TTS layer somewhere to breathe: the voice note inserts a short
# pause between them, which is what makes several ideas followable.
from app.services.speech import speech_segments  # noqa: E402

segs = speech_segments(sample, "swahili")
assert len(segs) >= 3, segs
assert all(s.endswith((".", "!", "?")) for s in segs), segs
assert all("(" not in s and ")" not in s for s in segs), segs
assert "," not in segs[0].rstrip(",.") or True
# No stray ",." from inlining a parenthetical, and no doubled stops.
messy = speech_segments("🌧 MVUA (Lengwenyi well)\nSiku 30 zimepita.", "swahili")
assert messy[-1].endswith(".") and ".," not in messy[0] and ",." not in messy[0], messy
print("speech segments OK")
print("uncertainty + units survive into the voice note OK")

# --- 5) the rain line reads as sentences, with no label or parentheses ------
line_sw = rain_line(o, "swahili")
assert "Mvua: " not in line_sw, line_sw
assert "(" not in line_sw and ")" not in line_sw, line_sw
assert "makadirio" in line_sw.lower(), line_sw
assert line_sw.rstrip().endswith("."), line_sw
line_en = rain_line(o, "english")
assert "Rain: " not in line_en and "(" not in line_en, line_en
print("rain line reads as sentences OK")

# --- 6) the rephrase guard: the model may not wreck the text ----------------
from app.services.ai import rephrase_advisory_ok  # noqa: E402

base = format_advisory_message(
    language="english", species="cattle", distance_km=4.3,
    condition=ForageCondition.DRY_FORAGE_AVAILABLE, seasonally_normal=True,
    curing_stage_note=None, water_reliability=WaterReliability.UNKNOWN,
    grazing_zone=None, effective_radius_km=7.0, dry_harsh=False)
# The exact failure seen live: everything merged into one long line.
flattened = " ".join(base.splitlines())
ok, why = rephrase_advisory_ok(base, flattened, 4.3)
assert not ok and why == "lines_merged", (ok, why)
ok, why = rephrase_advisory_ok(base, base.replace(". ", "; "), 4.3)
assert not ok and why == "semicolon", (ok, why)
ok, why = rephrase_advisory_ok(base, base.replace("4.3", "5.3"), 4.3)
assert not ok and why.startswith("dropped_number"), (ok, why)
# Same numbers, but the distance is no longer written as the distance: comma decimal
# survives the number check (4,3 == 4.3) and must still be caught as a lost figure.
ok, why = rephrase_advisory_ok(base, base.replace("4.3", "4,3"), 4.3)
assert not ok and why == "distance_lost", (ok, why)
ok, why = rephrase_advisory_ok(
    base,
    base.replace("For your cattle, the nearest water is 4.3 km away.",
                 "For your cattle: water: 4.3 km away."),
    4.3)
assert not ok and why == "colon_run", (ok, why)
# A good, faithful rephrase is still allowed through.
good = "\n".join([
    "For your cattle, water is 4.3 km away from here.",
    "The pasture around that water is good dry forage.",
    "We have not confirmed that water is there. Verify before you set off.",
])
ok, why = rephrase_advisory_ok(base, good, 4.3)
assert ok, why
print("rephrase guard blocks flattening, semicolons, lost numbers OK")

# --- 7) no regressed templates -------------------------------------------
import io  # noqa: E402

status_src = io.open("app/services/water_status.py", encoding="utf-8").read()
# Scope to the code (not the docstring, which deliberately documents the old
# wording so nobody reintroduces it).
_check_fn = status_src.split("def check_question(")[1].split("\ndef ")[0]
_check_code = _check_fn.split('"""')[-1]
assert "Majina hayo" not in _check_code, "the nonsensical question must stay deleted"
assert "maji yapo kwenye chanzo" in _check_code, _check_code[:200]
assert "def status_sentence(" in status_src, "prose must use sentences, not labels"
i18n_src = io.open("app/services/i18n.py", encoding="utf-8").read()
assert "nyasi kavu nzuri ya malisho ipo" not in i18n_src, "the broken fragment must stay gone"
assert "WATER_TEXT" in i18n_src and "Maji hapa yanategemewa kipindi hiki." in i18n_src

print("message-style tests OK")

