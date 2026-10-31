"""The pilot's ONE feature switch: what a pastoralist may see, and what he may not.

What this pins (all of it product decisions, so all of it belongs in a test):

  1. By default the WhatsApp menu offers ONLY the drought/pasture rows and
     registration. The weight tape, the herd estimate, ticks and worms, the grazing
     ledger, build status and the settings rows exist in the code but are not
     offered — a herder in a dry week should not have to read nine options.
  2. A hidden capability is refused by the DISPATCHER too, not just hidden from the
     menu: an old quick-reply button, an old number or a deep link must not reach
     it. The refusal is a real answer (what we do now), never silence.
  3. Settings stay ENABLED but UNLISTED: 'sauti'/'english' still work for a herder
     who learned them, and nobody new is asked to choose.
  4. The switch is overridable with ARDA_FEATURES and a bad value fails safe (the
     default menu, never an empty one).
  5. The service catalogue the AI may quote is filtered by the same switch, so the
     model can never advertise a service the router would refuse.

Runs without a database, without WhatsApp credentials and without network.
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.services import features, service_catalog  # noqa: E402

# --- 1) the default contract ------------------------------------------------
assert features.DEFAULT_FEATURES == ("drought", "pasture", "register")
visible = {s for s in features.SERVICE_FEATURE if features.is_listed(s)}
# 'change' belongs to registration (fix the point you registered) and has no row of
# its own; the five with rows are asserted in scripts/test_menu.py.
assert visible == {"rain", "depletion", "map", "location", "pin", "change"}, visible
for hidden in ("pest", "weight", "herd", "animals", "graze", "status"):
    assert not features.is_enabled(hidden), f"{hidden} must not run in this pilot"
    assert not features.is_listed(hidden), hidden
# Settings: reachable, never advertised.
for setting in ("voice", "language"):
    assert features.is_enabled(setting), setting
    assert not features.is_listed(setting), setting
# A row that is not a service at all (navigation, menu) is left to the router.
assert features.is_enabled("menu") and not features.is_listed("menu")
print("default: 5 listed rows (rain, depletion, map, location, pin); settings on but unlisted")

# --- 2) the catalogue the AI may quote is filtered the same way -------------
quoted = {row["service"] for row in service_catalog.describe("swahili")}
assert quoted == {"rain", "depletion", "map", "location", "pin", "change"}, quoted
listed_line = service_catalog.names("swahili")
for word in ("UZITO", "KUPE", "MALISHO YA LEO", "UJENZI"):
    assert word not in listed_line, f"hidden service advertised to the model: {word}"
assert "MALISHO YATAISHA" in listed_line and "MVUA" in listed_line, listed_line
ranked = service_catalog.for_question("malisho yataisha lini", "swahili", limit=3)
assert ranked and ranked[0]["service"] == "depletion", ranked
assert all(r["service"] not in {"pest", "weight"} for r in ranked), ranked
print("catalogue: hidden services are neither ranked nor described, depletion ranks first")

# --- 3) ARDA_FEATURES overrides, and fails safe ----------------------------
os.environ[features.ENV_VAR] = "livestock"
try:
    assert features.enabled_features() == ("livestock",)
    assert features.is_listed("pest") and not features.is_listed("rain")
    assert features.is_enabled("weight") and not features.is_enabled("depletion")
    # Settings are still reachable, still unlisted.
    assert features.is_enabled("language") and not features.is_listed("language")
finally:
    os.environ.pop(features.ENV_VAR, None)

for bad, expected in (("", features.DEFAULT_FEATURES),
                      ("   ", features.DEFAULT_FEATURES),
                      ("drought,banana", ("drought",)),
                      ("BANANA", features.DEFAULT_FEATURES)):
    os.environ[features.ENV_VAR] = bad
    try:
        assert features.enabled_features() == expected, (bad, features.enabled_features())
        # No readable value can ever empty the menu.
        assert features.is_listed("rain"), bad
    finally:
        os.environ.pop(features.ENV_VAR, None)


# --- 4) the dispatcher refuses a hidden service (no menu required) ----------
def _router_checks() -> None:
    from app.routers import whatsapp as wa

    sent: list[str] = []
    menu_calls = {"n": 0}
    original_text = wa.whatsapp_client.send_text
    original_menu = wa._show_menu
    original_weight = wa._start_weight_flow

    def _no_weight(*_a, **_k):
        raise AssertionError("a hidden service was actually started")

    class _Herder:
        phone_number = "254700000009"
        preferred_language = "swahili"
        primary_species = "cattle"
        water_source_id = None
        water_interval = "daily"
        voice_replies = False

    wa.whatsapp_client.send_text = lambda phone, text, **k: sent.append(text)
    wa._show_menu = lambda phone, p: menu_calls.__setitem__("n", menu_calls["n"] + 1)
    wa._start_weight_flow = _no_weight
    try:
        for service in ("weight", "herd", "pest", "graze", "status"):
            sent.clear()
            menu_calls["n"] = 0
            wa._run_service("254700000009", _Herder(), service)
            assert sent, f"{service}: a refused service must still answer"
            assert any("mambo mawili" in t for t in sent), (service, sent)
            assert menu_calls["n"] == 1, f"{service}: the menu must be offered after a refusal"
        # ... and an ENABLED service is not refused. 'language' is used here because
        # it answers from a constant: no database, no raster, nothing to wait for.
        sent.clear()
        wa._run_service("254700000009", _Herder(), "language")
        assert not any("mambo mawili" in t for t in sent), sent
        assert any("english" in t for t in sent), sent

        # The refusal names four things a herder can send. Every one of them must be
        # a word the router actually recognises: an answer that points at a dead end
        # is worse than the silence it replaced. (Found by review: "MALISHO" was
        # offered as the map word and is not a MAP keyword — "MAP" is.)
        for phrase, keywords in (("malisho yataisha", wa.DEPLETION_KEYWORDS),
                                 ("mvua", wa.RAIN_KEYWORDS),
                                 ("map", wa.MAP_KEYWORDS),
                                 ("pin", wa.PIN_KEYWORDS),
                                 ("when grass runs out", wa.DEPLETION_KEYWORDS)):
            assert any(k in phrase for k in keywords), (phrase, keywords)
        for lang, promised in (("swahili", ("MALISHO YATAISHA", "MVUA", "MAP", "PIN")),
                               ("english", ("WHEN GRASS RUNS OUT", "MVUA", "MAP", "PIN"))):
            reply = features.hidden_service_reply(lang)
            for word in promised:
                assert word in reply, (lang, word, reply)
                # ... and the phrase we just promised resolves to a real branch.
                lower = word.lower()
                assert (any(k in lower for k in wa.DEPLETION_KEYWORDS)
                        or any(k in lower for k in wa.RAIN_KEYWORDS)
                        or any(k in lower for k in wa.MAP_KEYWORDS)
                        or any(k in lower for k in wa.PIN_KEYWORDS)), (lang, word)
    finally:
        wa.whatsapp_client.send_text = original_text
        wa._show_menu = original_menu
        wa._start_weight_flow = original_weight


try:
    _router_checks()
    print("dispatcher: hidden services are refused with an answer + the menu, enabled ones run")
except ImportError as exc:
    # The router import needs the full app (rasterio etc.); on a host that cannot
    # import it the pure switch above is still checked, and we say so out loud.
    # Deliberately ImportError only: an assertion failure inside the checks must
    # FAIL the run, not be reported as a skip.
    print(f"router checks skipped ({type(exc).__name__}: {exc})")

print("\nFEATURE GATE OK")

print("override: ARDA_FEATURES switches features; an unreadable value keeps the default menu")
