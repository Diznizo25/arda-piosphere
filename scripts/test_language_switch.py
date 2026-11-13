"""The language switch: a herder can change how he hears us, in one tap.

What this pins — every line is a thing a herder hit in the field:

  1. There IS a way to change the language. Asked on WhatsApp, the menu's last row
     is 🌍 Lugha / Language, in its own section, in BOTH languages. A herder who
     cannot read our replies has no other way to say so, so it is not gated by
     ARDA_FEATURES (features.ALWAYS_LISTED).
  2. Tapping it gives him TWO BUTTONS (Kiswahili / English), not an instruction to
     type a word. The old answer — "Tuma 'english' kwa Kiingereza" — is a dead end
     for exactly the man who needs the switch: he cannot spell English. That is the
     failure this test exists for.
  3. Tapping a button actually changes the language: ONE column write, the
     confirmation and the menu that follows in the NEW language, and the in-memory
     row updated so the rest of that same turn already speaks it.
  4. It works from ANY step of ANY flow (the tapped id and the typed word), so a
     herder stuck in a guided flow in a language he does not read can stop and fix
     it. 'onboarding.name' must never store "lugha" as his name.
  5. The old habits keep working: typing 'english'/'kiingereza'/'swahili' and the
     legacy number 9 all reach the same switch.
  6. Nothing is ever silence: if the buttons cannot be sent the typed instruction
     goes out instead, and if the database write fails he still gets the switch for
     this conversation (fail-open, like every other write on this path).

Runs without a database, without WhatsApp credentials and without network: the
WhatsApp client, the state store and the language write are all local fakes.
"""
import os
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.routers import whatsapp as wa  # noqa: E402
from app.services import conversation, features, registration  # noqa: E402

# --- the fakes --------------------------------------------------------------
SENT: list[tuple[str, str]] = []
BUTTONS: list[tuple[str, list[tuple[str, str]]]] = []
PAYLOADS: list[dict] = []
WRITES: list[tuple[str, str]] = []
FAIL_BUTTONS = {"on": False}
FAIL_WRITE = {"on": False}

PHONE = "254700000123"


def _buttons(to, body, btns):
    if FAIL_BUTTONS["on"]:
        raise RuntimeError("WhatsApp rejected the buttons")
    BUTTONS.append((body, list(btns)))


def _set_language(phone, language):
    if FAIL_WRITE["on"]:
        raise RuntimeError("database is down")
    WRITES.append((phone, language))


wa.whatsapp_client.send_text = lambda to, body: SENT.append(("text", body))
wa.whatsapp_client.send_quick_reply_buttons = _buttons
wa.whatsapp_client.send_interactive_payload = lambda payload: PAYLOADS.append(payload)
registration.set_language = _set_language

conversation.get_state = lambda phone: (None, {})
conversation.set_state = lambda phone, state, data=None: None
conversation.clear_state = lambda phone: None
conversation.state_age_seconds = lambda phone: 0

# Landmark resolution needs wards + landmarks in the database, and this test is
# about the language switch: keep the turn off it entirely.
wa._resolve_message_place = lambda phone, p, text: (None, False)

# The free-text fallthrough would answer from the chat layer (database + model).
# Nothing in this test may reach it; a fake keeps that honest without a DB.
wa.chat = types.SimpleNamespace(answer=lambda *a, **k: None)


class Herder:
    id = "p-lang"
    phone_number = PHONE
    preferred_language = "swahili"
    primary_species = "cattle"
    water_source_id = None
    water_interval = "daily"
    voice_replies = False
    is_onboarded = True
    first_name = "Test"
    gap_hours = 30.0


def turn(text: str, language: str = "swahili", voice: bool = False):
    """One inbound message, with every outbound side effect captured."""
    SENT.clear()
    BUTTONS.clear()
    PAYLOADS.clear()
    WRITES.clear()
    p = Herder()
    p.preferred_language = language
    wa._handle_text(PHONE, p, text, voice=voice)
    return p


def menu_lang() -> str:
    """Which language the menu that just went out is written in."""
    assert PAYLOADS, "no menu was sent"
    body = PAYLOADS[-1]["interactive"]["body"]["text"]
    for lang, expected in wa.MENU_BODY.items():
        if body == expected:
            return lang
    raise AssertionError(f"menu body in no known language: {body!r}")


# --- 1) the menu offers the switch, in both languages -----------------------
for lang in ("swahili", "english"):
    sections = wa._menu_sections(lang)
    ids = [row[0] for _title, rows in sections for row in rows]
    assert ids[-1] == "svc:lang", (lang, ids)
    assert sections[-1][1][0][0] == "svc:lang", (lang, sections[-1])
assert wa.SERVICE_ALIASES["svc:lang"] == "language"
assert features.is_listed("language"), "the language row must always be listed"
os.environ[features.ENV_VAR] = "drought"  # the pilot's other extreme
try:
    assert features.is_listed("language"), "ARDA_FEATURES must not hide the language row"
    assert features.is_listed("rain") and not features.is_listed("pin")
finally:
    os.environ.pop(features.ENV_VAR, None)
print("menu: the Lugha row is last in both languages and survives ARDA_FEATURES OK")

# --- 2) tapping it asks with BUTTONS, never with homework -------------------
for entry, lang in (("svc:lang", "swahili"), ("9", "swahili"), ("svc:lang", "english")):
    turn(entry, lang)
    assert BUTTONS, f"{entry}: no buttons offered — the herder was left to type"
    body, btns = BUTTONS[-1]
    assert [b[0] for b in btns] == ["lang:swa", "lang:eng"], btns
    assert [b[1] for b in btns] == ["Kiswahili", "English"], btns
    # Bilingual body: usable by a man who cannot read the language he is stuck in.
    assert "Lugha" in body and "Language" in body, body
    assert "Kiswahili" in body and "English" in body, body
    assert "Tuma" not in body and "Send" not in body, \
        f"{entry}: the chooser must not be an instruction to type: {body!r}"
print("chooser: the menu row and the legacy number 9 answer with two one-tap buttons OK")

# --- 3) the tap changes the language, and PROVES it ------------------------
p = turn("lang:eng")
assert WRITES == [(PHONE, "english")], WRITES
assert p.preferred_language == "english", p.preferred_language
confirmation = [t for k, t in SENT if k == "text"][-1]
assert "English" in confirmation and "\u2705" in confirmation, confirmation
assert menu_lang() == "english", "the menu after the switch must be in the NEW language"

p = turn("lang:swa", language="english")
assert WRITES == [(PHONE, "swahili")], WRITES
assert p.preferred_language == "swahili"
assert any("Kiswahili" in t for _k, t in SENT), SENT
assert menu_lang() == "swahili"
print("switch: one column write, confirmation + menu in the new language, in-memory row updated OK")

# --- 4) choosing the language he already has -------------------------------
p = turn("lang:swa", language="swahili")
assert WRITES == [], f"a no-op switch must not write: {WRITES}"
assert p.preferred_language == "swahili"
assert any("Kiswahili" in t for _k, t in SENT), "he still gets an acknowledgement"
assert menu_lang() == "swahili"
print("no-op switch: no database write, still acknowledged, menu still sent OK")

# --- 5) the typed words and the old habits still work ----------------------
for text, expected, was in (("english", "english", "swahili"),
                            ("Kiingereza", "english", "swahili"),
                            ("swahili", "swahili", "english"),
                            ("kiswahili", "swahili", "english")):
    p = turn(text, was)
    assert WRITES == [(PHONE, expected)], (text, WRITES)
    assert p.preferred_language == expected, (text, p.preferred_language)
for text in ("lugha", "Lugha", "badilisha lugha", "change language", "LANGUAGE"):
    turn(text)
    assert BUTTONS, f"{text!r}: typing it must open the chooser"
    assert not WRITES, f"{text!r}: the chooser must not switch anything by itself"
print("typed words: 'english'/'kiingereza' still switch, 'lugha' opens the chooser OK")

# --- 6) it works from ANY step of ANY flow ---------------------------------
# A herder is half-way through registration, in Swahili, and does not read it.
reached = {"onboarding": 0}
real_onboarding = wa._handle_onboarding_step


def _count_onboarding(*_a, **_k):
    reached["onboarding"] += 1


wa._handle_onboarding_step = _count_onboarding
conversation.get_state = lambda phone: ("onboarding.name", {})
try:
    turn("lugha")
    assert BUTTONS, "mid-flow 'lugha' must open the chooser"
    assert reached["onboarding"] == 0, "'lugha' was swallowed by the onboarding step"
    p = turn("lang:eng")
    assert WRITES == [(PHONE, "english")], WRITES
    assert reached["onboarding"] == 0, "the tapped language must not be read as a name"
    assert p.preferred_language == "english"
finally:
    wa._handle_onboarding_step = real_onboarding
    conversation.get_state = lambda phone: (None, {})
print("any step: the switch escapes a live flow instead of being stored as a name OK")

# --- 7) never silence: the buttons fail, then the write fails --------------
# The two tracebacks printed below are EXPECTED: they are the router logging the
# failure it then recovers from. If they ever stop appearing, the fallbacks below
# are no longer being reached.
FAIL_BUTTONS["on"] = True
try:
    turn("svc:lang")
finally:
    FAIL_BUTTONS["on"] = False
assert not BUTTONS
assert SENT, "if the buttons cannot be sent, the typed instruction must go out"
assert any("english" in t.lower() for _k, t in SENT), SENT

FAIL_WRITE["on"] = True
try:
    p = turn("lang:eng")
finally:
    FAIL_WRITE["on"] = False
assert p.preferred_language == "english", "a failed write must still switch this conversation"
assert any("English" in t for _k, t in SENT), SENT
assert menu_lang() == "english"
print("fail-open: no buttons -> typed instruction; no database -> the switch still holds OK")

print("\nLANGUAGE SWITCH OK")
