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
  7. The predictors (migration 016: dry-period rate, near/far ring shape, NDMI
     curing) may refine the answer and explain it — never invent a number, and
     never speak while the reading they rest on is missing.

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

# --- 7) an honest start: not enough history is a STATUS, not a number ----
thin = depletion.forecast(series([44, 42]), today=TODAY)   # 2 samples, 7 days apart
assert thin.status == "no_history" and thin.days_left is None, thin
assert thin.usable_pct == 42, thin
close = [depletion.Sample(TODAY - timedelta(days=4), 44),
         depletion.Sample(TODAY - timedelta(days=2), 41),
         depletion.Sample(TODAY, 39)]
short = depletion.forecast(close, today=TODAY)             # 3 samples, 4-day span
assert short.status == "no_history", short
assert "tunaanza kupima" in depletion.line(thin), depletion.line(thin)
assert "still measuring" in depletion.line(short, "english")
# ... and with no measurement at all, not even that: silence.
assert depletion.line(depletion.forecast([], today=TODAY)) == ""
print("history: <3 samples or <12 days of span -> 'still measuring', never a number")

# --- 8) the predictors must LEAD the line, and never fake a number --------
# Everything migration 016 stores is optional; these seasons pin what each one is
# allowed to change - and what it is not.
def season(values, rain=None, ndmi=None, near=None, far=None, start=None):
    """Weekly samples (oldest first) carrying the 016 context (None = not measured)."""
    start = start or TODAY - timedelta(days=7 * (len(values) - 1))

    def pick(seq, i):
        return None if not seq else seq[i]

    return [depletion.Sample(day=start + timedelta(days=7 * i), usable_pct=v,
                             rain_14d_mm=pick(rain, i), ndmi=pick(ndmi, i),
                             near_pct=pick(near, i), far_pct=pick(far, i))
            for i, v in enumerate(values)]


# A green-up month then a dry slide: fitted as one line the ring reads FLAT ("it
# lasts"), which is the bug the dry-period rate exists to fix. The dry readings
# alone describe the collapse that is actually happening.
wet_then_dry = season([30, 40, 46, 44, 38, 32], rain=[45, 45, 45, 0, 0, 0])
flat_line = depletion.forecast(
    [depletion.Sample(day=s.day, usable_pct=s.usable_pct) for s in wet_then_dry],
    today=TODAY)
dry_line = depletion.forecast(wet_then_dry, today=TODAY)
assert flat_line.status == "stable" and flat_line.days_left is None, flat_line
assert dry_line.status == "projected" and dry_line.rate_source == "dry", dry_line
assert dry_line.slope_pp_week == dry_line.dry_slope_pp_week == -6.0, dry_line
assert dry_line.days_left_lo < dry_line.days_left_hi, dry_line
assert depletion.line(dry_line, "english") != depletion.line(flat_line, "english")

# ...but only while the ring is IN a dry spell. One wet reading and the dry rate
# is history: it is reported, never used to date anything.
still_wet = season([30, 40, 46, 44, 38, 32], rain=[45, 45, 0, 0, 0, 20])
wet_now = depletion.forecast(still_wet, today=TODAY)
assert wet_now.rate_source == "all" and wet_now.dry_slope_pp_week == -4.0, wet_now
assert wet_now.status == "stable" and wet_now.days_left is None, wet_now

# A GENTLER dry rate never replaces a steeper long-run one: when the fall happened
# during the wet readings, the dry stretch is the quiet part, not the collapse.
steep_then_calm = season([60, 40, 26, 24, 22], rain=[40, 40, 0, 0, 0])
calm = depletion.forecast(steep_then_calm, today=TODAY)
assert calm.rate_source == "all" and calm.dry_slope_pp_week == -2.0, calm
assert calm.days_left is not None and calm.days_left < 20, calm

# The SHAPE of the ring: near-water vs outer ring, and whether the gap is widening.
assert depletion.pressure(season([40, 41, 40, 41], near=[40, 40, 39, 40],
                                 far=[41, 41, 40, 41])).label == "even"
one_read = depletion.pressure(season([40], near=[18], far=[44]))
assert one_read.label == "grazing_legacy", one_read     # one image cannot show widening
scar = depletion.pressure(season([40, 39, 40, 38], near=[18, 18, 18, 18],
                                 far=[44, 44, 44, 44]))
assert scar.label == "grazing_legacy" and scar.gap_pp == 26.0, scar
eating = depletion.pressure(season([46, 42, 38, 34], near=[18, 16, 12, 8],
                                  far=[44, 44, 44, 44]))
assert eating.label == "grazing_recent" and eating.gap_change_pp == 10.0, eating
assert depletion.cause_of(eating) == "grazing"
assert depletion.cause_of(scar) == "grazing"
assert depletion.cause_of(depletion.Pressure()) == "unknown"   # nothing measured
assert depletion.forecast(season([58, 52, 46, 40, 34]), today=TODAY).cause == "unknown"

# The cause reaches the push, and the stable branch still carries the context.
grazed = depletion.forecast(season([46, 42, 38, 34], near=[18, 16, 12, 8],
                                   far=[44, 44, 44, 44]), today=TODAY)
assert grazed.cause == "grazing" and grazed.gap_pp == 36.0, grazed
assert grazed.is_alert and "near the water" in depletion.alert_message(grazed, "english")
assert not grazed.curing, grazed          # the share is already falling: no extra alarm

# CURING: grass losing water while the shares have not moved - a sentence, no date.
curing = depletion.forecast(season([40, 39, 40, 41], ndmi=[-0.10, -0.13, -0.17, -0.21]),
                            today=TODAY)
assert curing.status == "stable" and curing.days_left is None and curing.curing, curing
assert "kukauka" in depletion.line(curing, "swahili"), depletion.line(curing, "swahili")
assert "dry out" in depletion.line(curing, "english"), depletion.line(curing, "english")
assert not depletion.is_curing(series([60, 50, 40, 30]), -10.0), \
    "a share that is already falling needs no smoke alarm"
print("predictors: dry-period rate, ring shape and curing each say only what they measured")

print("\nDEPLETION FORECAST OK")
