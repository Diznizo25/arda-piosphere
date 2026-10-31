"""When will the pasture in a ring run out — and when must we refuse to answer?

This is the honesty test for the depletion forecast (app/services/depletion.py).
Every rule below exists because the alternative is a herder walking 15 km on the
strength of a number we made up:

  1. A date is ALWAYS a range, and it always carries the word for "estimate".
  2. Two readings are not a trend, and neither is one satellite pass: with no
     history the answer is "I am still measuring", not a number.
  3. A flat or greening ring gets NO date (there is nothing to warn about), and a
     ring already at run-out gets urgency instead of arithmetic.
  4. A projection past the horizon is not dated — we date weeks, not seasons.
  5. Rain is a separate sentence about uncertainty, never folded into the date.
  6. No message may claim certainty, and none may name a drug or a dose (same rule
     as the grazing ledger: we advise on grass, never on animal health).

Synthetic seasons only: no database, no network, any month of the year.
"""
import pathlib
import sys
from datetime import date, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.services import depletion  # noqa: E402

TODAY = date(2026, 10, 18)


def series(values, start=TODAY - timedelta(days=28), step=7):
    """Weekly samples, oldest first, ending at `start + step*(n-1)`."""
    return [depletion.Sample(day=start + timedelta(days=step * i), usable_pct=v)
            for i, v in enumerate(values)]


# --- 1) a declining season: a range, a label, and no invented precision -----
dep = depletion.forecast(series([58, 52, 46, 40, 34]), today=TODAY)
assert dep.status == "projected", dep
assert dep.usable_pct == 34 and dep.samples == 5 and dep.span_days == 28, dep
assert dep.slope_pp_week is not None and dep.slope_pp_week < 0, dep
assert dep.days_left is not None and 0 < dep.days_left <= depletion.MAX_HORIZON_DAYS, dep
assert dep.days_left_lo < dep.days_left_hi, dep
assert dep.confidence == "high", dep
sw = depletion.line(dep, "swahili")
en = depletion.line(dep, "english")
for text in (sw, en):
    assert "kadirio" in text or "estimate" in text, text
assert f"siku {dep.days_left_lo} hadi {dep.days_left_hi}" in sw, sw
assert f"{dep.days_left_lo} to {dep.days_left_hi} days" in en, en
for forbidden in ("hakika", "certainly", "exactly", "100%"):
    assert forbidden not in sw and forbidden not in en, (forbidden, sw, en)
print(f"declining: {dep.samples} samples -> {dep.days_left_lo}-{dep.days_left_hi} days, "
      f"{dep.confidence} confidence, labelled as an estimate OK")

# --- 2) not enough history: say so ----------------------------------------
for few in (series([]), series([44]), series([44, 41])):
    d = depletion.forecast(few, today=TODAY)
    assert d.status == "no_history", (few, d)
    assert d.days_left is None, d
    assert not d.is_alert, d
    if few:
        assert "tunaanza kupima" in depletion.line(d, "swahili"), d
    else:
        # No measurement at all: no sentence at all. Silence beats "no data".
        assert depletion.line(d, "swahili") == "", d

# A count rule and a span rule are separate on purpose: three readings inside four
# days are still one satellite image.
short = [depletion.Sample(TODAY - timedelta(days=4), 60),
         depletion.Sample(TODAY - timedelta(days=2), 58),
         depletion.Sample(TODAY, 56)]
assert depletion.forecast(short, today=TODAY).status == "no_history", "4 days is one image"


# --- 3) flat, greening, already spent ------------------------------------
flat = depletion.forecast(series([40, 41, 39, 40, 41]), today=TODAY)
assert flat.status == "stable" and flat.days_left is None, flat
assert "hayapungui" in depletion.line(flat, "swahili"), depletion.line(flat, "swahili")
greening = depletion.forecast(series([20, 26, 33, 41, 50]), today=TODAY)
assert greening.status == "stable" and greening.days_left is None, greening

spent = depletion.forecast(series([30, 22, 14, 9]), today=TODAY)
assert spent.status == "already_low" and spent.days_left == 0, spent
assert spent.is_alert and "⚠️" in depletion.line(spent, "swahili"), spent
print("no false alarm: flat/greening -> no date; already spent -> urgency, not arithmetic")

# --- 4) a date we refuse to give ----------------------------------------
slow = depletion.forecast(series([90, 89, 88, 87, 86]), today=TODAY)
assert slow.status == "beyond_horizon", slow
assert slow.days_left is None, slow
assert str(depletion.MAX_HORIZON_DAYS) in depletion.line(slow, "english"), slow
print(f"horizon: a decline past {depletion.MAX_HORIZON_DAYS} days is not dated")

# --- 5) rain is uncertainty, not arithmetic -----------------------------
wet = depletion.forecast(series([58, 52, 46, 40, 34]), today=TODAY,
                         rain_mm=25.0, rain_onset=TODAY + timedelta(days=3))
dry = depletion.forecast(series([58, 52, 46, 40, 34]), today=TODAY,
                         rain_mm=2.0, rain_onset=TODAY + timedelta(days=3))
assert wet.days_left == dry.days_left, (wet.days_left, dry.days_left)
assert wet.rain_may_recover and not dry.rain_may_recover
assert "Mvua inatarajiwa" in depletion.line(wet, "swahili"), wet
assert "Mvua" not in depletion.line(dry, "swahili"), dry
print("rain: reported as a chance of recovery; the projected date is untouched")

# --- 6) hygiene: duplicates, junk, silence ------------------------------
dupes = [depletion.Sample(TODAY, 40), depletion.Sample(TODAY, 35),
         depletion.Sample(TODAY - timedelta(days=7), 45)]
cleaned = depletion.clean(dupes)
assert len(cleaned) == 2 and cleaned[-1].usable_pct == 35.0, cleaned
assert depletion.clean([None, object(), "x"]) == []
assert depletion.line(None) == "" and depletion.line(depletion.forecast([], today=TODAY)) == ""
assert depletion.alert_message(depletion.forecast(series([40, 41, 40, 41]), today=TODAY)) == ""
assert depletion.fit([]) == (None, 0.0, 0, 0)

alert = depletion.alert_message(dep, "swahili", place="Kipsing")
assert alert.startswith("🌿 MALISHO YATAISHA LINI"), alert
assert "Kipsing" in alert and "makadirio" in alert, alert
for word in ("dawa", "sindano", "dose", "injection"):
    assert word not in alert.lower(), (word, alert)
print("hygiene: duplicates collapse, junk is ignored, non-alerts produce no push")

print("\nDEPLETION FORECAST OK")

print("history: <3 samples or <12 days of span -> 'still measuring', never a number")
