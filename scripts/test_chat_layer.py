"""Chat-layer tests: routing, the number guardrail, the disease guardrail, and
fail-open behaviour. Pure — no DB, no network, no model.

The point of these tests is the SAFETY of the layer, not its charm: an invented
number or a guessed diagnosis reaching a herder is the failure mode we care about.

Run: python scripts/test_chat_layer.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.services import chat  # noqa: E402


class FakeHerder:
    phone_number = "+254700000001"
    preferred_language = "swahili"
    primary_species = "cattle"
    water_source_id = "11111111-1111-1111-1111-111111111111"
    water_interval = "daily"


# --- 1) number extraction and the traceability rule --------------------------
assert chat.numbers_in("siku 30 bila mvua, 0.1 mm") == {"30", "0.1"}
assert chat.numbers_in({"a": 1.3, "b": ["x12", None]}) == {"1.3", "12"}
assert chat.numbers_in(None) == set()
assert chat.numbers_in(True) == set()  # booleans are not numbers to report
assert chat.numbers_in("0,5 mm") == {"0.5"}  # decimal comma normalised

allowed = chat.allowed_numbers({"km": 1.3, "deficit": -98.5}, "ng'ombe 40")
assert {"1.3", "98.5", "40"} <= allowed
assert "300" not in allowed
# Rounding tolerance is applied at VALIDATION time, not here: the allowed set holds
# literals (1.3, 98.5), and the validator separately accepts "1 mm" for 1.3 and
# "98%" for -98.5 (see _number_is_traceable).
assert "1" not in allowed and "98" not in allowed
print("number extraction + allowed set OK")

# --- 2) the guardrail: invented numbers are rejected -------------------------
ok, why = chat.validate_answer("Mvua itanyesha baada ya siku 3.", {"3"}, "swahili")
assert ok, why
ok, why = chat.validate_answer("Maji yapo umbali wa 12 km.", {"1.3"}, "swahili")
assert not ok and why.startswith("invented_number"), why
ok, why = chat.validate_answer("Utabiri: mvua 500 mm.", {"1.3"}, "swahili")
assert not ok and why == "invented_number:500", why
# Rounding of a real fact is allowed (1.3 -> "1 mm", -98.5 -> "98%").
ok, _ = chat.validate_answer("Mvua ni 1 mm pekee, 98% pungufu.", {"1.3", "98.5"}, "swahili")
assert ok
print("invented-number guardrail OK")

# --- 3) other validation rules ----------------------------------------------
assert chat.validate_answer("", set(), "swahili") == (False, "empty")
ok, why = chat.validate_answer("x" * 900, set(), "swahili")
assert not ok and why == "too_long"
ok, why = chat.validate_answer("Angalia https://x.com kwa maji ya siku 3.", {"3"}, "swahili")
assert not ok and why == "markdown_or_url"
ok, why = chat.validate_answer("**Maji** yapo siku 3.", {"3"}, "swahili")
assert not ok and why == "markdown_or_url"
# Language: a Swahili herder must not get a pure English answer.
ok, why = chat.validate_answer("The water is far away from your herd today.", {"3"}, "swahili")
assert not ok and why == "not_swahili", why
ok, why = chat.validate_answer("Maji yapo karibu na wewe leo.", {"3"}, "english")
assert not ok and why == "not_english", why
print("length / markdown / language rules OK")

# --- 4) routing picks the right sections (no model, deterministic) -----------
assert chat.intent_sections("mvua itanyesha lini?") == ["rain"]
assert chat.intent_sections("maji yapo wapi karibu nami") == ["water"]
assert chat.intent_sections("malisho yakoje?") == ["pasture"]
assert set(chat.intent_sections("ng'ombe wangu wanahitaji maji ngapi")) == {"water", "herd"}
assert chat.intent_sections("habari yako") == []
print("intent routing OK")

# --- 5) the disease guardrail: never a diagnosis, always a route to a vet ----
assert chat.is_disease_question("mbuzi wangu ana homa na kuhara")
assert chat.is_disease_question("my cattle are sick with a fever")
assert chat.is_disease_question("mnyama amekufa ghafla")
assert not chat.is_disease_question("mvua itanyesha lini")
reply = chat.disease_reply("swahili")
assert "ugonjwa" in reply.lower() and "afisa wa mifugo" in reply.lower()
assert "hawezi" in reply.lower() or "haiwezi" in reply.lower(), reply
assert chat.disease_reply("english").lower().count("vet") >= 1
print("disease guardrail OK")

# --- 6) the knowledge base answers what the raw model refused ---------------
hits = chat.match_knowledge("ng'ombe 40 wanahitaji maji ngapi kwa siku?")
assert [h["id"] for h in hits] == ["water_cattle"], hits
assert chat.match_knowledge("kwa nini bot haina bei ya soko")[0]["id"] == "market_timing"
assert chat.match_knowledge("habari yako leo") == []
for entry in chat.knowledge_entries():
    assert entry.get("source"), f"{entry['id']} has no source - rule 1 of the file"
    assert entry.get("sw") and entry.get("en"), f"{entry['id']} needs both languages"
print("knowledge base + sourcing rule OK")

# --- 7) the deterministic answer stands on its own --------------------------
facts = {
    "rain": {"dry_spell_days": 30, "observed_30d_mm": 0.1, "normal_30d_mm": 6.87,
             "deficit_pct": -98.5, "season": "dry_season", "forecast_days": 15,
             "forecast_total_mm": 1.3, "onset_date": None, "confidence": "moderate"},
    "water": {"found": True, "distance_km": 4.2, "reliability": "seasonal"},
    "pasture": {"condition": "dry_forage_available", "seasonally_normal": True, "vci": 42},
    "guidance": [{"text": "Ng'ombe mmoja anahitaji takriban lita 30-45 kwa siku.",
                  "source": "FAO", "reviewed": False}],
}
sw = chat.deterministic_answer(facts, "swahili")
assert "siku 30 bila mvua" in sw and "6.9" in sw and "makadirio" in sw, sw
assert "4.2" in sw, "the bundle included water facts, so they are spoken"
# A rain-only bundle stays a rain answer - the routing (not a hidden filter) is
# what keeps replies on topic.
rain_only = chat.deterministic_answer({"rain": facts["rain"]}, "swahili")
assert "4.2" not in rain_only and "km" not in rain_only, rain_only
en = chat.deterministic_answer({"rain": facts["rain"]}, "english")
assert "30 days without real rain" in en and "estimate" in en.lower(), en
# A curated entry that has not been reviewed must SAY so - never presented as fact.
with_guidance = chat.deterministic_answer({"guidance": facts["guidance"],
                                           "water": facts["water"]}, "swahili")
assert "hakikiwa na mtaalam: bado" in with_guidance, with_guidance
assert "4.2" in with_guidance
empty = chat.deterministic_answer({}, "swahili")
assert "menu" in empty
print("deterministic answer OK")

# --- 8) end-to-end answer(): model proposes, validator decides, fallback saves
def gather_rain(herder, question, sections):
    return {"rain": facts["rain"]}


def llm_good(system, facts_json, question):
    assert "FACTS=" not in system            # the prompt must be the rules only
    assert "6.87" in facts_json, "facts JSON must be handed to the model"
    return "Siku 30 bila mvua ya maana; kawaida ni 7 mm (makadirio)."


def llm_hallucinating(system, facts_json, question):
    return "Mvua itanyesha siku 3 zijazo, jumla 240 mm."     # 240 is invented


def llm_english(system, facts_json, question):
    return "There will be no rain in the next 15 days at all."


def llm_empty(system, facts_json, question):
    return None


herder = FakeHerder()
out = chat.answer(herder, "mvua itanyesha lini?", "swahili",
                  gather=gather_rain, llm=llm_good, counter=lambda phone: 0)
assert out.startswith("Siku 30 bila mvua"), out
print("accepted grounded model answer OK")

# Every rejection path must land on the deterministic answer, not on silence.
for bad, label in ((llm_hallucinating, "invented number"),
                   (llm_english, "wrong language"),
                   (llm_empty, "empty model reply")):
    out = chat.answer(herder, "mvua itanyesha lini?", "swahili",
                      gather=gather_rain, llm=bad, counter=lambda phone: 0)
    assert out and "240" not in out and "no rain in the next" not in out, (label, out)
    assert "siku 30 bila mvua" in out.lower(), (label, out)
print("rejection -> deterministic fallback OK")

# Over the daily cap the model is skipped entirely, but the herder still answers.
def llm_must_not_be_called(system, facts_json, question):
    raise AssertionError("the model must not be called over the daily cap")


out = chat.answer(herder, "mvua itanyesha lini?", "swahili",
                  gather=gather_rain, llm=llm_must_not_be_called,
                  counter=lambda phone: chat.DAILY_LLM_LIMIT)
assert out and "siku 30" in out.lower(), out
print("daily cap respected OK")

# No facts at all -> None, so the caller shows the services menu.
assert chat.answer(herder, "habari yako", "swahili", gather=lambda *a: {},
                   llm=llm_must_not_be_called, counter=lambda p: 0) is None
# A disease question never reaches the model, even with facts available.
out = chat.answer(herder, "mbuzi wangu ana homa na kuhara", "swahili",
                  gather=gather_rain, llm=llm_must_not_be_called, counter=lambda p: 0)
assert "afisa wa mifugo" in out.lower(), out
print("no-facts -> menu, disease -> vet route OK")

# --- 9) wiring: whatsapp asks the chat layer before showing the menu ---------
import io  # noqa: E402

wa = io.open("app/routers/whatsapp.py", encoding="utf-8").read()
tail = wa.split("gt_intent = ai.classify_report")[1]
assert "chat.answer(" in tail, "free text must reach the chat layer"
assert tail.index("chat.answer(") < tail.index("_show_menu(phone, pastoralist)"), \
    "the menu is the fallback, not the first response"
assert "except Exception" in tail.split("chat.answer(")[1][:400], \
    "chat failures must never break the webhook"

ai_src = io.open("app/services/ai.py", encoding="utf-8").read()
assert "def grounded_answer(" in ai_src
assert "max_completion_tokens" in ai_src and "reasoning_effort" in ai_src
print("whatsapp + ai wiring OK")

print("chat-layer tests OK")

