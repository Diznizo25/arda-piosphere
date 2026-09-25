"""The water loop: the queue question, the fan-out rules and the repair closure.

Everything here is the pure half of app/services/water_loop.py — the DB helpers are
exercised through the app, not here. The point of these tests is that the rules a
herder will feel are explicit: who gets told what, and how often.

Runs anywhere (no DB, no network).
"""
import pathlib
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.services import water_loop as wl  # noqa: E402

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)

# --- 1) the queue digit ----------------------------------------------------
assert wl.queue_for_digit("1") == wl.QUEUE_SHORT
assert wl.queue_for_digit(" 2 ") == wl.QUEUE_LONG
assert wl.queue_for_digit("12") is None, "a herd size is not a queue answer"
assert wl.queue_for_digit("maji yapo") is None
assert wl.queue_for_digit("") is None
print("queue digits: 1 short / 2 long, anything else ignored OK")

# --- 2) wording: a question, a sentence and a thank-you -------------------
q = wl.queue_question("swa")
assert "1 fupi" in q and "2 ndefu" in q, q
assert "long" in wl.queue_question("eng").lower()
assert wl.queue_sentence(wl.QUEUE_SHORT, "swa") == "Foleni ni fupi."
assert wl.queue_sentence(wl.QUEUE_LONG, "swa", "Kipsing well") == \
    "Foleni kwenye Kipsing well ni ndefu."
assert wl.queue_sentence(None, "swa").startswith("Hatujui"), wl.queue_sentence(None)
assert "Asante" in wl.thanks_for_queue(wl.QUEUE_LONG, "swa")
assert "Thank you" in wl.thanks_for_queue(wl.QUEUE_LONG, "eng")
print("queue wording: question, sentence, thanks OK")

# --- 3) a queue reading is about now --------------------------------------
assert wl.queue_is_fresh(NOW - timedelta(hours=3), now=NOW) is True
assert wl.queue_is_fresh(NOW - timedelta(hours=30), now=NOW) is False
assert wl.queue_is_fresh(None, now=NOW) is False
naive = (NOW - timedelta(hours=2)).replace(tzinfo=None)
assert wl.queue_is_fresh(naive, now=NOW) is True, "naive timestamps must not crash"
print("queue freshness: 24h window, naive timestamps tolerated OK")

# --- 4) who gets told, and how often --------------------------------------
assert wl.should_fanout("functional", "unknown", None, now=NOW) is True
assert wl.should_fanout("dry", "functional", None, now=NOW) is True
assert wl.should_fanout("dry", "dry", None, now=NOW) is False, \
    "the same verdict twice is not news"
assert wl.should_fanout("unknown", "dry", None, now=NOW) is False
assert wl.should_fanout("functional", "dry", NOW - timedelta(hours=2),
                        now=NOW) is False, "inside the cooldown"
assert wl.should_fanout("functional", "dry", NOW - timedelta(hours=20),
                        now=NOW) is True, "outside the cooldown"
assert wl.FANOUT_COOLDOWN_HOURS == 12
print("fan-out rules: change-only, 12h cooldown, never 'unknown' OK")

# --- 5) anonymity and honesty in the fan-out message ----------------------
msg = wl.fanout_message("functional", "Kipsing well", "swa", queue=wl.QUEUE_LONG)
assert "Mchungaji mwenzio amethibitisha" in msg, msg
assert "maji yapo" in msg, msg
assert "Foleni ni ndefu." in msg, msg
assert not re.search(r"\+?\d[\d\s-]{7,}", msg), f"no phone numbers in a fan-out: {msg}"
assert "Mchungaji mwenzio" not in wl.fanout_message("dry", None, "eng"), "language split"
eng = wl.fanout_message("dry", None, "eng")
assert "Another herder has confirmed" in eng and "dry" in eng, eng
# Our own voice never claims the water is there: a herder confirmed it.
assert "we confirm" not in eng.lower()
print("fan-out message: anonymous, attributes the report, carries the queue OK")

# --- 6) the repair path ----------------------------------------------------
assert wl.looks_like_repair("imekarabatiwa")
assert wl.looks_like_repair("Pampu imekarabatiwa sasa")
assert wl.looks_like_repair("the pump is fixed")
assert wl.looks_like_repair("maji yamerudi")
assert not wl.looks_like_repair("imekauka")
assert not wl.looks_like_repair("maji yapo")
assert "imekarabatiwa" in wl.repair_message("Kipsing well", "swa")
assert "Asante" in wl.repair_confirmed("swa")
print("repair: keywords recognised, confirmation and notice worded OK")

print("\nWATER LOOP OK")
