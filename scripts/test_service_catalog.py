"""The AI knows our services -- and can only name services we actually run.

A herder asking "unaweza kufanya nini?" used to get the static menu (or "sijaelewa").
The model could not answer because the facts bundle had no section for our own
services, and letting a model describe our services ungrounded is how a bot invents
capabilities. So the catalogue is data in app/services/service_catalog.py, and this
test pins the two directions that keep it honest:

    router -> catalogue   every service the menu can reach is described
    catalogue -> router   every service described has a real branch to run

Plus: a capability question is answered deterministically (no model, no menu), in
the herder's language, without internal identifiers or invented numbers.

    python scripts/test_service_catalog.py
"""
from __future__ import annotations

import inspect
import json
import sys

sys.path.insert(0, ".")

from app.services import chat, service_catalog  # noqa: E402

ids = [s.id for s in service_catalog.SERVICES]
assert len(ids) == len(set(ids)), f"duplicate service ids: {ids}"
assert set(service_catalog.BY_ID) == set(ids)
assert service_catalog.ROUTER_SERVICE_IDS == tuple(ids)

for svc in service_catalog.SERVICES:
    assert svc.say_sw.strip() and svc.say_en.strip(), svc.id
    assert svc.what_sw.strip() and svc.what_en.strip(), svc.id
    assert len(svc.what_sw) <= 80 and len(svc.what_en) <= 80, (svc.id, svc.what_sw)
    assert not any(ch.isdigit() for ch in svc.what_sw + svc.what_en), \
        f"{svc.id}: a service description is not a measurement"
    assert svc.answers.strip(), svc.id
print(f"catalogue: {len(ids)} services, both languages, no invented numbers OK")

# Ranking is deterministic and never returns something outside the catalogue.
rows = service_catalog.for_question("maji yapo kwenye kisima?", "swahili",
                                    limit=2, prefer=("location",))
assert rows[0]["service"] == "location", rows
assert all(r["service"] in service_catalog.BY_ID for r in rows)
rows_en = service_catalog.for_question("when will it rain", "english", limit=2)
assert rows_en[0]["service"] == "rain", rows_en
assert rows_en[0]["say"] == "RAIN"
print("ranking: 'maji' -> MAJI, 'rain' -> RAIN, nothing outside the catalogue OK")


class _Herder:
    phone_number = "254700000001"
    preferred_language = "swahili"
    primary_species = "cattle"
    water_source_id = None
    water_interval = "daily"


# The activity log is the only DB touch on this path; keep the test hermetic.
chat._log_chat = lambda *a, **k: None

answered = chat.answer(
    _Herder(), "unaweza kufanya nini?", gather=chat.gather_facts,
    llm=lambda *a, **k: None, counter=lambda p: 0, memory={},
    remember=lambda *a, **k: None,
)
assert answered, "a capability question must be ANSWERED, not turned into a menu"
assert "MAJI" in answered and "MVUA" in answered, answered
assert "malisho" in answered.lower(), answered
assert "svc:" not in answered and "_" not in answered, answered
assert len(answered) <= 600, f"{len(answered)} chars: too long to read in a pasture"
assert "location" not in answered.lower() or "eneo" in answered.lower(), answered
print("capability question: answered from the catalogue, in Swahili, no menu OK")


# --- the model may PHRASE the catalogue, never extend it --------------------
def _gather(*a, **k):
    return {"services": {"services": [{"say": "MAJI", "what": "hali ya chanzo chako"}],
                         "how_to_ask": "Tuma MAJI."},
            "herder": {"has_confirmed_water_point": True}}


seen: dict = {}


def _llm(system, facts_json, question, context=""):
    seen["facts"] = json.loads(facts_json)
    return "Ninaweza kukusaidia: MAJI - hali ya chanzo chako. Tuma MAJI."


phrased = chat.answer(_Herder(), "unaweza kufanya nini?", gather=_gather, llm=_llm,
                      counter=lambda p: 0, memory={}, remember=lambda *a, **k: None)
assert phrased and phrased.startswith("Ninaweza"), phrased
assert seen["facts"]["services"]["services"][0]["say"] == "MAJI", seen
print("model path: catalogue reached the prompt and its wording was accepted OK")


def _bad_llm(*a, **k):
    return "Tuma svc:change au namba 999."


rejected = chat.answer(_Herder(), "unaweza kufanya nini?", gather=_gather, llm=_bad_llm,
                       counter=lambda p: 0, memory={}, remember=lambda *a, **k: None)
assert rejected and "999" not in rejected and "svc:" not in rejected, rejected
print("guardrail: an invented service/number is rejected, fallback used OK")


class _HerderEn:
    phone_number = "254700000002"
    preferred_language = "english"
    primary_species = "camel"
    water_source_id = "w1"
    water_interval = "daily"


english = chat.answer(_HerderEn(), "what can you do?", gather=chat.gather_facts,
                      llm=lambda *a, **k: None, counter=lambda p: 0, memory={},
                      remember=lambda *a, **k: None)
assert english and "WATER" in english and "RAIN" in english, english
print("english: the same catalogue answers in English OK")


# --- the catalogue cannot drift from the router -----------------------------
try:
    from app.routers import whatsapp as wa

    reachable = set(wa.SERVICE_ALIASES.values()) | set(wa.MENU_NUMBERS.values())
    described = set(service_catalog.ROUTER_SERVICE_IDS)
    navigation = set(service_catalog.NAVIGATION_ALIASES)
    missing = reachable - described - navigation
    assert not missing, f"the AI cannot describe services the router runs: {sorted(missing)}"
    for nav, target in service_catalog.NAVIGATION_ALIASES.items():
        assert nav in reachable, f"navigation alias {nav!r} is not reachable"
        assert target is None or target in described, (nav, target)
    body = inspect.getsource(wa._run_service)
    for sid in sorted(described):
        assert f'"{sid}"' in body, f"catalogue promises {sid!r}; _run_service has no branch"
    for sections in (wa.MENU_SECTIONS, wa.MENU_SECTIONS_EN):
        for _title, rows in sections:
            for rid, _label, _desc in rows:
                assert rid in wa.SERVICE_ALIASES, f"{rid} has no service behind it"
                target = wa.SERVICE_ALIASES[rid]
                assert target in described or target in navigation, (rid, target)
    catalogue_ids = {mid for s in service_catalog.SERVICES for mid in s.menu_ids}
    menu_ids = {rid for sections in (wa.MENU_SECTIONS, wa.MENU_SECTIONS_EN)
                for _t, rows in sections for rid, _l, _d in rows}
    navigation_rows = {rid for rid in menu_ids
                       if wa.SERVICE_ALIASES.get(rid) in navigation}
    unexplained = menu_ids - catalogue_ids - navigation_rows
    assert not unexplained, f"menu rows nobody describes: {sorted(unexplained)}"
    print("router <-> catalogue: every reachable service described, every menu id known OK")
except Exception as exc:  # noqa: BLE001
    print(f"router checks skipped ({type(exc).__name__}: {exc})")

print("\nSERVICE CATALOGUE OK")

