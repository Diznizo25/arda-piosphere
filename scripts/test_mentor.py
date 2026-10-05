"""The mentor layer: the guard that lets a model speak, and nothing else.

The point of this suite is the list of ways a rewrite can LOSE. A rewrite that reads
beautifully and drops a figure, invents a figure, turns an estimate into a fact,
loses the route to the vet, flips language, flattens the text or pads it out is worse
than the flat text it replaced — because a herder acts on it either way, and only one
of the two is ours.

Pure module: no DB, no network, no COG. The model call is injected.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import io  # noqa: E402

from app.services import mentor  # noqa: E402

# --- 1) the voice file is present, reviewable and service-aware --------------
v = mentor.load_voice()
assert v.get("status") == "needs_review", "the file must say it needs a native review"
assert len(str(v.get("persona") or "")) > 80
assert v.get("rules") and len(v["rules"]) >= 6
for kind in ("graze", "pest", "water", "note", "default"):
    assert (v.get("services") or {}).get(kind, {}).get("goal"), kind
system = mentor.build_system("graze", "swa")
assert str(v["persona"]).strip()[:40] in system
assert "graze" not in system.lower() or "worth" in system.lower()
assert "Swahili" in system, "the language must be named in the system prompt"
assert all(r[:20] in system for r in v["rules"]), "every hard rule must reach the model"
print("voice: persona + rules + per-service goals, reviewable status OK")

# --- 2) the guard: every way a rewrite can LOSE ------------------------------
BASE = "\n".join([
    "🌿 MALISHO YA LEO",
    "📍 Mlipopanda ~km 14.0 kutoka manyatta yako",
    "🛰 Picha ya 05 Sep: nyasi kavu",
    "Anatumia MJ 50 (kutembea 7, joto 4)",
    "Anakusanya MJ 26 (nyasi 4.1 kg kavu)",
    "Upungufu MJ 24",
    "✔ Hamia km 4 kaskazini (nyasi mbichi inayokua)",
    "Kadirio kutoka satellite — si vipimo vya mnyama wako.",
])
GOOD = "\n".join([
    "🌿 MALISHO YA LEO",
    "📍 Mlipopanda km 14.0 kutoka manyatta yako.",
    "🛰 Picha ya 05 Sep: nyasi kavu.",
    "Kwa kila mnyama: anatumia MJ 50 (kutembea 7, joto 4), anakusanya MJ 26 (nyasi 4.1 kg kavu), yaani upungufu wa MJ 24.",
    "Hamna cha kununua kwanza — hamia km 4 kaskazini, kuna nyasi mbichi inayokua.",
    "Kadirio kutoka satellite — si vipimo vya mnyama wako.",
])

ok, why = mentor.insight_ok(BASE, GOOD, lang="swa")
assert ok, why

cases = [
    ("", "empty"),
    ("Mpe dawa ya minyoo mara moja.", "forbidden_word"),
    (GOOD + "\nHii ni siku 3 ya ukame.", "new_number"),
    (GOOD.replace("MJ 24", ""), "dropped_number"),
    (GOOD.replace("Kadirio kutoka satellite — si vipimo vya mnyama wako.", ""), "safety_label_lost"),
    ("🌿 GRAZING TODAY\nYou walked 14.0 km. Picture of 05 Sep: dry grass.\n"
     "Spends MJ 50 (walking 7, heat 4).\nGets MJ 26 (4.1 kg dry matter).\n"
     "Gap MJ 24.\nKadirio satellite — not a measurement.", "language_flipped"),
    (GOOD.replace("kuna nyasi mbichi inayokua", "kuna nyasi mbichi; inayokua"), "semicolon"),
    (GOOD.replace("Hamna cha kununua kwanza — hamia km 4 kaskazini, kuna nyasi mbichi inayokua.",
                  "Kwanza: hamia: km 4 kaskazini"), "colon_run"),
    (GOOD.replace("🌿 MALISHO YA LEO", "## MALISHO YA LEO"), "markdown"),
    (GOOD + "\nAngalia https://example.com kwa mengi.", "new_url"),
    (GOOD + "\n" + ("nyasi " * 400), "too_long"),
]
for text, expected in cases:
    ok, why = mentor.insight_ok(BASE, text, lang="swa")
    assert not ok, f"the guard let through: {expected}"
    assert why.split(":")[0] == expected, (expected, why)

# Flattening is tested on its own text, because the colon rule would fire first on a
# message that has colons and we want to know the FLATTENING rule works by itself.
FLAT_BASE = "\n".join([
    "Maji yapo kwenye kisima cha Kipsing.",
    "Malisho mazuri yapo km 6 kaskazini.",
    "Ng'ombe wako 18 wanahitaji lita 400 kwa siku.",
    "Kadirio tu.",
])
ok, why = mentor.insight_ok(FLAT_BASE, " ".join(FLAT_BASE.splitlines()), lang="swa")
assert not ok and why == "flattened", why
ok, why = mentor.insight_ok(FLAT_BASE, "\n".join(FLAT_BASE.splitlines()[:2]), lang="swa")
assert not ok and why.startswith("dropped_number"), why
print("guard: empty, drug word, invented/dropped number, lost safety label, language flip,")
print("       semicolon, colon run, markdown, new link, flattening, padding — all blocked OK")

# --- 3) fail-open, twice over: no model, and a model that misbehaves ---------
real_chat = mentor.ai._chat
sent: list = []


def _fake_chat(system: str, user: str):
    sent.append((system, user))
    return None


mentor.ai._chat = _fake_chat
try:
    assert mentor.rewrite("graze", BASE, lang="swa") == BASE, "no reply must keep the text"
finally:
    mentor.ai._chat = real_chat
assert sent, "the model was never asked"
assert "FACTS=" in sent[0][1] and "TEXT:" in sent[0][1], sent[0][1][:120]
assert "kadirio" in sent[0][1].lower() or "Kadirio" in sent[0][1]

mentor.ai._chat = lambda system, user: "Mpe dawa ya minyoo."
try:
    assert mentor.rewrite("graze", BASE, lang="swa") == BASE, \
        "a rewrite that breaks the guard must be thrown away"
finally:
    mentor.ai._chat = real_chat

mentor.ai._chat = lambda system, user: GOOD
try:
    assert mentor.rewrite("graze", BASE, lang="swa") == GOOD, "a faithful rewrite is used"
finally:
    mentor.ai._chat = real_chat

# the switch: one env var, no deploy
from app.config import get_settings  # noqa: E402

settings = get_settings()
assert hasattr(settings, "mentor_insights_enabled"), "no opt-out flag on settings"
mentor.ai._chat = lambda system, user: GOOD
settings.mentor_insights_enabled = False
try:
    assert mentor.rewrite("graze", BASE, lang="swa") == BASE, \
        "with the flag off the model must not even be consulted"
finally:
    settings.mentor_insights_enabled = True
    mentor.ai._chat = real_chat
print("fail-open: no model, a misbehaving model, and the off switch all keep the text OK")

# --- 4) the facts handed to the model come from the computed objects ---------
from app.services import forage  # noqa: E402

quality = forage.quality_from_bands(0.08, 0.30, 0.06, -0.05, snapshot_as_of="05 Sep")
advice = forage.advise(quality, species="cattle", head_count=18, walk_distance_km=14.0,
                       temp_max_c=38.0, from_manyatta=True)
facts = mentor.graze_facts(advice)
assert facts["verdict"] == "deficit" and facts["balance_mj"] < 0
assert facts["walk_km"] == 14.0 and facts["head_count"] == 18
assert facts["snapshot"] == "05 Sep" and facts["forage"] == "nyasi kavu"
assert facts["balance_band_mj"][0] < facts["balance_band_mj"][1]
assert mentor.graze_facts(object()) == {}, "a broken object must not raise"


class _Window:
    key = "ticks"
    tier = "high"
    score = 3

    def reason_lines(self, lang):
        return ["mvua 16 mm siku 7"]


class _Outlook:
    highest_tier = "high"
    windows = (_Window(),)
    active = (_Window(),)      # the outlook's own property: the windows that count


pf = mentor.pest_facts(_Outlook())
assert pf["highest_tier"] == "high" and pf["windows"][0]["key"] == "ticks"
assert mentor.pest_facts(object()) == {}
print("facts: the ledger and the pest window are flattened for grounding OK")

# --- 5) it is WIRED, and only where a rewrite is safe -----------------------
root = pathlib.Path(__file__).resolve().parents[1]
wa = io.open(root / "app/routers/whatsapp.py", encoding="utf-8").read()
assert 'mentor.rewrite("graze"' in wa, "the grazing ledger is not mentor-voiced"
assert 'mentor.rewrite("pest"' in wa, "the pest message is not mentor-voiced"
assert 'mentor.rewrite("water"' in wa, "the welcome-back insights are not mentor-voiced"
# A question must never be rewritten: a reworded question breaks the one-tap answer
# it belongs to (the state machine matches digits, and the herder answers what he read).
import re  # noqa: E402

for call in re.findall(r"mentor\.rewrite\((.{0,240}?)\)\n", wa, re.S):
    assert "question" not in call.lower(), f"a question reached the mentor pass: {call[:80]}"
assert "forage.quality_question" not in wa.split("mentor.rewrite")[1][:600]
grazing_src = io.open(root / "app/services/grazing_flow.py", encoding="utf-8").read()
assert "mentor" not in grazing_src, "questions/prompts stay deterministic"

dev = io.open(root / "app/routers/dev.py", encoding="utf-8").read()
assert '@router.post("/mentor")' in dev, "no /dev/mentor probe to review the wording"
cfg = io.open(root / "app/config.py", encoding="utf-8").read()
assert "mentor_insights_enabled" in cfg
note = io.open(root / "scripts/send_weekly_note.py", encoding="utf-8").read()
assert '"--mentor"' in note and "args.mentor" in note, "the weekly note has no mentor mode"
assert 'if args.mentor:' in note, "the weekly note must be opt-in (one call per herder)"
# The advisory keeps its own stricter rephrase guard (distance-specific); the mentor
# layer generalises it to the services that had nothing.
assert "rephrase_advisory" in io.open(root / "app/services/advisory_service.py",
                                      encoding="utf-8").read()
print("wiring: graze + pest + welcome-back rewritten, questions untouched, opt-in batch OK")

print("\nMENTOR LAYER OK")

