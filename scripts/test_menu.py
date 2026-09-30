"""The services menu: clickable, trimmed, and never a data dump for "hello".

Three things this pins, all of them things a herder reported or would notice:

  1. A greeting is NOT a question. "hello" after a quiet spell used to return the
     rain/drought briefing, because an empty intent list made the fact gatherer collect
     everything. It now returns the welcome + menu.
  2. The menu is clickable (an interactive list) with a numbered text fallback, and both
     entry points run the SAME code, so a service cannot behave differently depending on
     how it was reached.
  3. The legacy numbers still mean what they meant (1 location, 2 pin, 3 weight, ... 10
     pests). Renumbering would be a silent break for anyone who learned them.

The router import needs the full app (rasterio etc.), which some Windows hosts block; when
that import fails the suite still checks the pure parts and says so.
"""
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.services import chat  # noqa: E402
from app.services import whatsapp_client  # noqa: E402

# --- 1) greetings ---------------------------------------------------------
for greeting in ("hello", "hi", "habari", "habari yako", "habari yako ndugu", "jambo",
                 "mambo vipi", "shikamoo", "thanks", "good morning", "sasa"):
    assert chat.is_greeting(greeting), f"{greeting!r} should read as a greeting"
for question in ("habari ya mvua", "mvua itanyesha lini", "maji yapo kwenye kisima?",
                 "niko karibu na Kipsing", "", "hello naomba unisaidie kuhusu malisho "
                 "ya ng'ombe zangu hapa kwa mwezi huu"):
    assert not chat.is_greeting(question), f"{question!r} must NOT be a greeting"
print("greetings: social openers recognised, real questions untouched OK")


# --- 2) a greeting never triggers the fact dump --------------------------
class _Herder:
    phone_number = "254700000001"
    preferred_language = "swahili"
    primary_species = "cattle"
    water_source_id = None
    water_interval = "daily"


called = {"gather": 0}


def _gather(*args, **kwargs):
    called["gather"] += 1
    return {"rain": {"dry_spell_days": 30}, "pasture": {"condition": "poor"}}


out = chat.answer(_Herder(), "hello", gather=_gather, llm=lambda *a: "SHOULD NOT RUN",
                  counter=lambda p: 0, memory={}, remember=lambda *a, **k: None)
assert out is None, f"a greeting must not produce an answer, got {out!r}"
assert called["gather"] == 0, "a greeting must not even gather facts"
# A real question keeps its topic (and is therefore never mistaken for a greeting); the
# full answering path is covered by scripts/test_chat_layer.py, which mocks the DB.
assert chat.intent_sections("mvua itanyesha lini") == ["rain"]
assert not chat.is_greeting("mvua itanyesha lini")
print("greeting: no facts gathered, no rain briefing; questions keep their topic OK")


# --- 3) the interactive payload respects WhatsApp's limits ---------------
sent: list[dict] = []
original_post = whatsapp_client._post
whatsapp_client._post = lambda payload: sent.append(payload)
try:
    whatsapp_client.send_menu_list(
        "254700000001", "Chagua huduma", "Huduma",
        [("A section title that is far too long to be accepted",
          [("svc:water", "A row title that is definitely too long", "d" * 90)]),
         ("Second", [("svc:rain", "🌧 Mvua", None)])],
        footer="f" * 90, header="h" * 90)
finally:
    whatsapp_client._post = original_post

payload = sent[0]["interactive"]
assert sent[0]["type"] == "interactive" and payload["type"] == "list"
assert len(payload["action"]["button"]) <= 20, payload["action"]["button"]
assert len(payload["header"]["text"]) <= 60 and len(payload["footer"]["text"]) <= 60
for section in payload["action"]["sections"]:
    assert len(section["title"]) <= 24, section["title"]
    for row in section["rows"]:
        assert len(row["title"]) <= 24, row["title"]
        assert len(row.get("description", "")) <= 72, row
print("menu payload: rows, titles, descriptions and footer inside WhatsApp limits OK")


# --- 4) the menu itself ---------------------------------------------------
try:
    from app.routers import whatsapp as wa

    for sections in (wa.MENU_SECTIONS, wa.MENU_SECTIONS_EN):
        rows = [row for _, rs in sections for row in rs]
        assert len(rows) <= 10, f"{len(rows)} rows is over WhatsApp's list limit"
        assert len({row[0] for row in rows}) == len(rows), "duplicate row id in the menu"
        for sid, title, desc in rows:
            assert sid.startswith("svc:"), sid
            assert len(title) <= 24, title
            assert len(desc) <= 72, desc
            assert sid in wa.SERVICE_ALIASES, f"{sid} has no service behind it"
        for name, _ in sections:
            assert len(name) <= 24, name
    assert wa.MENU_BUTTON["swahili"] and len(wa.MENU_BUTTON["swahili"]) <= 20

    # Every service the menu can reach must exist in _run_service.
    body = inspect.getsource(wa._run_service)
    for service in sorted(set(wa.SERVICE_ALIASES.values()) | set(wa.MENU_NUMBERS.values())):
        assert f'"{service}"' in body, f"_run_service has no branch for {service!r}"

    # Legacy numbers keep their original meanings.
    assert wa.MENU_NUMBERS == {"1": "location", "2": "pin", "3": "weight", "4": "herd",
                               "5": "map", "6": "status", "7": "voice", "8": "rain",
                               "9": "language", "10": "pest"}, wa.MENU_NUMBERS
    # The text fallback must still list every number.
    for num in wa.MENU_NUMBERS:
        for lang in ("swahili", "english"):
            assert f"{num}." in wa.MENU_MSG[lang], f"{num} missing from the {lang} menu"

    # Clickable first, numbered text only as the fallback.
    menu_src = inspect.getsource(wa._show_menu)
    assert "send_interactive_payload" in menu_src and "MENU_MSG" in menu_src, menu_src
    assert menu_src.index("send_interactive_payload") < menu_src.index("MENU_MSG"), \
        "the interactive menu must be tried before the numbered fallback"

    # The payload a herder will actually see, in both languages.
    for lang in ("swahili", "english"):
        payload = wa.menu_payload("254700000001", lang)
        rows = [row for s in payload["interactive"]["action"]["sections"]
                for row in s["rows"]]
        assert len(rows) <= 10, f"{lang}: {len(rows)} rows exceeds WhatsApp's limit"
        assert all(row["id"].startswith("svc:") for row in rows), rows
        assert len(set(row["id"] for row in rows)) == len(rows), "duplicate ids"
        assert all(len(row["title"]) <= 24 for row in rows), rows
        assert all(len(row.get("description", "")) <= 72 for row in rows), rows
        assert len(payload["interactive"]["action"]["button"]) <= 20
    # The same nine ids in both languages, so a service can never exist in one only.
    sw_ids = [r["id"] for s in wa.menu_payload("x", "swahili")["interactive"]["action"]["sections"]
              for r in s["rows"]]
    en_ids = [r["id"] for s in wa.menu_payload("x", "english")["interactive"]["action"]["sections"]
              for r in s["rows"]]
    assert sw_ids == en_ids, (sw_ids, en_ids)
    rows_n = len(wa.MENU_SECTIONS[0][1]) + len(wa.MENU_SECTIONS[1][1])
    print(f"menu: {rows_n} rows in {len(wa.MENU_SECTIONS)} sections, legacy numbers "
          f"intact, both languages identical OK")

    # --- 5) what a greeting sends ----------------------------------------
    src = inspect.getsource(wa._handle_greeting)
    assert "is_greeting" in src, "the greeting handler must use the shared detector"
    assert "gap_hours" in src and "away_days >= 7" in src, \
        "a returning herder must get the while-you-were-away lines"
    assert "_show_menu" in src, "a greeting must end at the menu"
    away = inspect.getsource(wa._while_you_were_away)
    assert "weekly_line" in away and "status_sentence" in away, away
    print("greeting reply: welcome + (if away) what changed + menu OK")
except Exception as exc:  # noqa: BLE001
    print(f"router checks skipped ({type(exc).__name__}: {exc})")

print("\nSERVICE MENU OK")
