"""The weekly note: shape, the 24-hour window, and the once-a-week rule.

Pure module tests for app/services/weekly_note.py plus the wiring assertions that
keep the sender honest (dry run by default, one note per herder per week, the note
ends with the one-tap question). Runs anywhere: no DB, no network.
"""
import io
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.services import weekly_note as wn  # noqa: E402

# --- 1) the shape: same every week, one question at the end ----------------
note = wn.build_note(
    lang="swa",
    place="Lengwenyi well",
    water_line="Maji yanapatikana kwenye chanzo hiki.",
    lines=["Siku 12 zimepita bila mvua ya maana.",
           "Mvua ya siku 7. Kwako 3.0 mm. Kipsing well 18.0 mm upande wa Kaskazini.",
           "🐛 Kupe: hatari ni juu wiki hii. Kuwa makini na uangalie mifugo yako."],
    question="Je, maji yapo kwenye chanzo hicho sasa? Jibu namba moja.\n1 maji yapo",
    map_url="https://example.org/mapview/?id=abc",
)
assert note.startswith("🐐 TAARIFA YA WIKI — Lengwenyi well"), note
assert "Kipsing well 18.0 mm" in note
assert "kupe" in note.lower()
assert "Ramani: https://example.org/mapview/?id=abc" in note
assert note.rstrip().endswith("1 maji yapo"), "the one-tap question must close the note"
assert note.count("Jibu namba moja") == 1, "exactly one question per note"
print("weekly note shape: header, lines, map link, one question OK")

# --- 2) empty weeks are short, never padded -------------------------------
thin = wn.build_note(lang="eng", water_line="Water is available at this point.",
                     question="Is there water? Reply with one number.")
assert "no data" not in thin.lower()
assert thin.count("\n\n") == 2, thin
assert "Lengwenyi" not in thin
print("thin week: nothing invented, nothing padded OK")

# --- 3) language split ----------------------------------------------------
assert wn.build_note(lang="eng", water_line="Water available.",
                     question="Q?").startswith("🐐 WEEKLY NOTE")
assert "wiki hii" in wn.week_label("swa") and "this week" == wn.week_label("eng")
print("languages: swa/eng titles and labels OK")

# --- 4) WhatsApp's 24-hour window ----------------------------------------
assert wn.can_message_now(0.5) is True
assert wn.can_message_now(23.9) is True
assert wn.can_message_now(24.1) is False
assert wn.can_message_now(None) is False, "never messaged us = needs a template"
print("24h window: in-window only, unknown never assumed OK")

# --- 5) once a week ------------------------------------------------------
assert wn.already_sent("2026-09-21", "2026-09-21") is True
assert wn.already_sent("2026-09-21", "2026-09-14") is False
assert wn.already_sent("2026-09-21", None) is False
print("once a week: replays and retries cannot double-send OK")

# --- 6) the sender keeps its promises ------------------------------------
src = io.open("scripts/send_weekly_note.py", encoding="utf-8").read()
assert 'add_argument("--send", action="store_true"' in src, \
    "the sender must be a dry run unless --send is passed"
assert "can_message_now" in src, "the 24h window must be checked before sending"
assert "set_last_advisory_water_source" in src, \
    "a one-tap reply must be bound to the point it is about"
assert "outside_24h_window" in src, "out-of-window herders must be recorded, not lost"
wf = io.open(".github/workflows/weekly-note.yml", encoding="utf-8").read()
assert 'cron: "0 5 * * 1"' in wf, "the note must be a weekly appointment"
assert "github.event_name" in wf, "the schedule sends; a manual run needs send=true"
print("sender wiring: dry-run default, window check, weekly schedule OK")

print("\nWEEKLY NOTE OK")
