"""
"When will the grass in my ring run out?"

WHY THIS FILE EXISTS
--------------------
This is the question behind every decision a herder makes in a dry spell: when to
move, which way to go, whether to sell. We answer it from the only thing we
actually measure over time — the pasture class mix inside HIS species ring, read
from the stored satellite overview (`map_renderer.pasture_overlay_status`). Each
read is stored as a sample (`app/services/pasture_history.py`), so the picture
gets sharper every week instead of restarting each time.

HOW THE DATE IS MADE
--------------------
  1. usable% = green% + dry% inside the ring — what an animal can actually eat.
  2. Fit a straight line through the samples (least squares, x = days).
  3. Project the day usable% reaches RUNOUT_PCT: the point where walking to that
     ring stops paying. NOT "zero grass" — no one waits for zero.
  4. REFUSE to answer when the data cannot support an answer:
       * fewer than MIN_SAMPLES or a span shorter than MIN_SPAN_DAYS
         -> "no_history" (we say we are still measuring, we do not invent a rate)
       * a slope that is not downhill  -> "stable"
       * a projected date past MAX_HORIZON_DAYS -> "beyond_horizon"
  5. Rain forecast before the projected date is reported as a CHANCE OF RECOVERY.
     It never quietly moves the date: a shower that may or may not fall cannot be
     arithmetic.

Every date leaves as a RANGE plus the word for "estimate", because the shares
come from rounded pixel counts and a single date would claim a precision that is
not in the data. Pure module — no DB, no network, no WhatsApp — so
`scripts/test_depletion.py` can run any season against it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# usable% at or below which a ring is no longer worth the walk. Deliberately not
# zero: at 10% usable the herd spends more energy walking than the ring returns,
# which is the actual decision point (see config/forage_energy.yaml's intake
# plateau for the same reasoning on the animal side).
RUNOUT_PCT = 10.0

MIN_SAMPLES = 3          # two points make a line through noise
MIN_SPAN_DAYS = 12       # ~one satellite refresh; anything shorter is one image
MAX_HORIZON_DAYS = 60    # we date weeks, not seasons
RAIN_MAY_RECOVER_MM = 12.0   # a forecast this wet can green the ring up again

# Percentage points per week below which a "decline" is noise between two
# satellite passes (the class shares are rounded to whole percent).
NOISE_PP_PER_WEEK = 0.6


@dataclass(frozen=True)
class Sample:
    """One day's pasture reading for one ring."""
    day: date
    usable_pct: float


@dataclass(frozen=True)
class Depletion:
    """Everything a message needs, already computed and already labelled."""
    status: str                    # no_history | stable | already_low | projected | beyond_horizon
    usable_pct: float | None = None
    slope_pp_week: float | None = None
    days_left: int | None = None       # best estimate, from today
    days_left_lo: int | None = None    # the range we are willing to say out loud
    days_left_hi: int | None = None
    samples: int = 0
    span_days: int = 0
    confidence: str = "low"        # high | moderate | low
    rain_may_recover: bool = False
    rain_onset: date | None = None

    @property
    def is_alert(self) -> bool:
        """Worth pushing to a herder without being asked?"""
        if self.status == "already_low":
            return True
        return (self.status == "projected" and self.days_left is not None
                and self.days_left <= MAX_HORIZON_DAYS)


def clean(samples) -> list[Sample]:
    """Sorted, de-duplicated by day (later reading wins), None-safe."""
    by_day: dict[date, float] = {}
    for s in samples or ():
        try:
            if s is None or s.usable_pct is None:
                continue
            by_day[s.day] = float(s.usable_pct)
        except (AttributeError, TypeError, ValueError):
            continue
    return [Sample(d, by_day[d]) for d in sorted(by_day)]


def fit(samples) -> tuple[float | None, float, int, int]:
    """Least-squares slope in percentage points per DAY.

    Returns (slope, r2, n, span_days). slope is None when a line cannot be fitted.
    r2 is 0..1 and is used only to decide how loudly we may speak.
    """
    pts = clean(samples)
    if len(pts) < 2:
        return None, 0.0, len(pts), 0
    x0 = pts[0].day
    xs = [(p.day - x0).days for p in pts]
    ys = [p.usable_pct for p in pts]
    n = len(xs)
    span = xs[-1] - xs[0]
    if span <= 0:
        return None, 0.0, n, span
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None, 0.0, n, span
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    ss_tot = sum((y - my) ** 2 for y in ys)
    intercept = my - slope * mx
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 0.0 if ss_tot <= 0 else max(0.0, min(1.0, 1.0 - ss_res / ss_tot))
    return slope, r2, n, span


def forecast(samples, today: date | None = None, runout_pct: float = RUNOUT_PCT,
             horizon_days: int = MAX_HORIZON_DAYS,
             rain_mm: float | None = None, rain_onset: date | None = None) -> Depletion:
    """The depletion answer for one ring, from its samples.

    Every branch that cannot answer SAYS SO with a status the caller can render,
    instead of returning a number it does not have.
    """
    pts = clean(samples)
    today = today or date.today()
    usable_now = round(pts[-1].usable_pct) if pts else None

    if usable_now is not None and usable_now <= runout_pct:
        # The ring is already spent: today's state is the answer, and it is more
        # urgent than any projection.
        rain_recovers = (rain_mm is not None and rain_mm >= RAIN_MAY_RECOVER_MM)
        return Depletion(status="already_low", usable_pct=usable_now, days_left=0,
                         days_left_lo=0, days_left_hi=0, samples=len(pts),
                         span_days=0, confidence="high",
                         rain_may_recover=rain_recovers, rain_onset=rain_onset)

    slope, r2, n, span = fit(pts)
    if slope is None or n < MIN_SAMPLES or span < MIN_SPAN_DAYS:
        # Honest start: we are still measuring. "I do not know yet" is the only
        # sentence that keeps the next answer believable.
        return Depletion(status="no_history", usable_pct=usable_now,
                         samples=n, span_days=span, confidence="low",
                         rain_onset=rain_onset)

    per_week = slope * 7.0
    if per_week >= -NOISE_PP_PER_WEEK:
        # Flat or greening: there is no date to give, and inventing one would be
        # the fastest way to lose a herder who walks out and finds grass.
        return Depletion(status="stable", usable_pct=usable_now,
                         slope_pp_week=round(per_week, 1), samples=n, span_days=span,
                         confidence="moderate" if r2 >= 0.3 else "low",
                         rain_onset=rain_onset)

    days = (usable_now - runout_pct) / abs(slope)
    if days > horizon_days:
        return Depletion(status="beyond_horizon", usable_pct=usable_now,
                         slope_pp_week=round(per_week, 1), samples=n, span_days=span,
                         confidence=_confidence(n, span, r2), rain_onset=rain_onset)

    # A range, not a date: the shares are whole percentages of rounded pixels, so
    # the honest window is roughly a quarter either side of the estimate.
    best = max(0, int(round(days)))
    lo = max(0, int(days * 0.75))
    hi = max(lo + 1, int(round(days * 1.25)))
    earliest = today + timedelta(days=lo)
    rain_recovers = bool(
        rain_mm is not None and rain_mm >= RAIN_MAY_RECOVER_MM
        and rain_onset is not None and rain_onset <= earliest
    )
    return Depletion(status="projected", usable_pct=usable_now,
                     slope_pp_week=round(per_week, 1), days_left=best,
                     days_left_lo=lo, days_left_hi=hi, samples=n, span_days=span,
                     confidence=_confidence(n, span, r2),
                     rain_may_recover=rain_recovers, rain_onset=rain_onset)


def _confidence(n: int, span: int, r2: float) -> str:
    """How loudly may we say it? High needs several samples across a month AND a
    line that actually fits; a noisy scatter is never spoken with confidence."""
    if n >= 5 and span >= 28 and r2 >= 0.5:
        return "high"
    if n >= 3 and span >= MIN_SPAN_DAYS and r2 >= 0.2:
        return "moderate"
    return "low"



_CONF = {
    # Even a well-fitted projection is a satellite ESTIMATE: the word stays, only
    # the width of the caveat changes with the confidence.
    "high": (" (kadirio)", " (estimate)"),
    "moderate": (" (kadirio)", " (estimate)"),
    "low": (" (kadirio pana — vipimo bado vichache)",
            " (rough estimate — few readings yet)"),
}


def line(dep: Depletion | None, lang: str = "swahili") -> str:
    """One or two sentences about depletion, ready for WhatsApp.

    Rules the test enforces: a date is ALWAYS a range and always carries the word
    for estimate; nothing is said when there is no measurement at all; a chance of
    rain is a separate sentence, never folded into the date.
    """
    if dep is None or dep.usable_pct is None:
        return ""
    sw = lang != "english"
    n = dep.usable_pct
    conf = _CONF.get(dep.confidence, ("", ""))[0 if sw else 1]

    if dep.status == "already_low":
        base = (f"⚠️ Malisho ya ring yako yamekwisha kwa kiasi kikubwa "
                f"(asilimia {n} yanaweza kulika)." if sw else
                f"⚠️ The pasture in your ring is largely spent "
                f"({n}% usable).")
        return f"{base} " + ("Anza kuhamia eneo lingine sasa." if sw
                             else "Start moving to another area now.")

    if dep.status == "stable":
        body = (f"Malisho ya ring yako: asilimia {n} yanaweza kulika, na hayapungui "
                f"kwa kasi kwa sasa" if sw else
                f"Your ring: {n}% usable pasture, and it is not falling fast right now")
        return body + conf + ("." if sw else ".")

    if dep.status == "beyond_horizon":
        body = (f"Malisho ya ring yako: asilimia {n}. Kwa mwendo huu yatadumu zaidi "
                f"ya siku {MAX_HORIZON_DAYS} zijazo" if sw else
                f"Your ring: {n}% usable. At this rate it lasts more than "
                f"{MAX_HORIZON_DAYS} days")
        return body + conf + "."

    if dep.status == "no_history":
        return (f"Malisho ya ring yako: asilimia {n} yanaweza kulika leo. Bado "
                f"tunaanza kupima mwenendo, hivyo siwezi kusema yatakapoisha — "
                f"nitakujulisha." if sw else
                f"Your ring: {n}% usable today. I am still measuring the trend, so I "
                f"cannot yet say when it runs out — I will tell you.")

    lo, hi = dep.days_left_lo or 0, dep.days_left_hi or 0
    if sw:
        body = (f"Kwa mwenendo wa vipimo vya wiki zilizopita, malisho ya ring yako "
                f"yanaweza kuisha katika siku {lo} hadi {hi} zijazo{conf}.")
        if dep.rain_may_recover:
            body += " Mvua inatarajiwa kabla ya hapo — hali inaweza kubadilika."
        return body
    body = (f"At the rate measured over the past weeks, the pasture in your ring may "
            f"run out in {lo} to {hi} days{conf}.")
    if dep.rain_may_recover:
        body += " Rain is expected before then — the picture may change."
    return body


def alert_message(dep: Depletion | None, lang: str = "swahili",
                  place: str | None = None) -> str:
    """The unprompted message (weekly push): the date, the reason, one action."""
    if dep is None or not dep.is_alert:
        return ""
    sw = lang != "english"
    head = "🌿 MALISHO YATAISHA LINI" if sw else "🌿 WHEN WILL THE PASTURE RUN OUT"
    if place:
        head = f"{head} — {place}"
    parts = [head, line(dep, lang)]
    if dep.status in ("projected", "already_low"):
        parts.append("Panga kuhamia eneo lenye malisho, au punguza idadi ya wanyama "
                     "unaoenda nao." if sw else
                     "Plan to move to an area with pasture, or take fewer animals.")
    parts.append("Haya ni makadirio ya satelaiti, si vipimo vya nyasi." if sw else
                 "This is a satellite estimate, not a measurement of the grass.")
    return "\n\n".join(p for p in parts if p)


__all__ = [
    "RUNOUT_PCT", "MAX_HORIZON_DAYS", "MIN_SAMPLES", "MIN_SPAN_DAYS",
    "RAIN_MAY_RECOVER_MM", "NOISE_PP_PER_WEEK",
    "Sample", "Depletion", "clean", "fit", "forecast", "line", "alert_message",
]
