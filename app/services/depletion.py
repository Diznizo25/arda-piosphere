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

WHAT LEADS THE LINE (added in migration 016)
--------------------------------------------
A straight line through usable% is the SLOWEST link in a chain we already hold.
Grass does not disappear before it dries, and it does not dry evenly:

  1. rain_14d_mm / soil moisture  -> is this even a dry spell?
  2. NDMI                         -> the grass is curing (2-4 weeks of warning)
  3. NDVI/SATVI class mix         -> the reading we have been dating
  4. usable% crossing RUNOUT_PCT  -> the event

Three things are therefore computed here from the stored samples. All of them are
optional, and all of them stay silent when the data is not there:

  * dry_slope_pp_week - the rate measured on DRY-period samples only. The
    unconditional fit mixes wet-season green-up with the dry-season slide, and one
    green-up month flattens the line to "stable" exactly when a collapse has
    begun. When a dry-period rate exists and is steeper, it is the rate the
    projection uses (rate_source == "dry").
  * cause / gap_pp - WHY the ring is going. Grazing is heaviest at the water
    point, so a ring being eaten from the water outward has less forage near the
    water than at its edge. That gap is measured by the SATELLITE, not by counting
    animals: registration is slow, and a predictor that needs a head count would
    be silent for most herds. A head count is a refinement, never a requirement.
  * curing - NDMI falling while the class mix has NOT moved yet. Nothing is dated
    from it (the shares have not fallen); it is a sentence, not arithmetic.

None of the three may move a date on its own. Only the measured decline may, and
only inside the horizon.

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

# --- what makes a reading a DRY-period reading ------------------------------
# A sample taken after this much rain (or less) in the previous 14 days is a dry
# spell: grazing demand is unchanged while growth has stopped, so that segment's
# slope is the one that describes a collapse. Averaged with wet-season growth the
# same ring reads "stable" - which is the bug this constant exists to fix.
DRY_WINDOW_MM = 2.0
# Three dry-period readings across two satellite refreshes is the minimum for a
# rate. Below that it is one image and an opinion.
DRY_MIN_SAMPLES = 3

# --- grazing pressure, measured spatially, never by head count --------------
# A ring eaten from the water outward has at least this much less usable forage
# near the water than at its edge (percentage points). Below it the decline is
# even across the ring, which is what a seasonal dry-down looks like.
CAUSE_GAP_PP = 12.0
# ...and to call the pressure RECENT rather than the old scar every water point
# carries, the gap must have widened by at least this much since the oldest
# reading that measured both zones.
CAUSE_GAP_CHANGE_PP = 8.0
CAUSE_GAP_MIN_SPAN_DAYS = 14

# --- curing: the warning that arrives before the shares move ----------------
# NDMI per week at or below this (index units) while usable% has not moved means
# the grass is losing water before it loses reflectance: pasture that still maps
# as "dry forage" today maps as bare in a few weeks.
NDMI_CURING_PER_WEEK = -0.01
NDMI_MIN_SAMPLES = 3


@dataclass(frozen=True)
class Sample:
    """One day's pasture reading for one ring.

    Only day + usable_pct are required: an older caller (and the tests) may pass
    just the share. Everything else is what migration 016 started storing so the
    forecast can be predicted instead of merely fitted.
    """
    day: date
    usable_pct: float
    # --- why it is going: every one is optional and silent when absent -------
    ndmi: float | None = None          # grass moisture: falls BEFORE the class mix
    vci: float | None = None           # poor FOR THE SEASON, or just poor?
    near_pct: float | None = None      # usable% inside 40% of the radius (daily walk)
    far_pct: float | None = None       # usable% in the outer ring
    patch_pct: float | None = None     # share of the ring in its largest usable patch
    # context of the reading: a wet-season sample must not flatten a dry-season slide
    rain_14d_mm: float | None = None


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
    # --- what leads the line (the module docstring explains each one) ---------
    dry_slope_pp_week: float | None = None  # measured on dry-period readings only
    rate_source: str = "all"                # all | dry - which rate the date used
    cause: str = "unknown"                  # season | grazing | unknown
    gap_pp: float | None = None             # far% - near%: grass away from the water
    curing: bool = False                    # NDMI falling before the shares moved

    @property
    def is_alert(self) -> bool:
        """Worth pushing to a herder without being asked?"""
        if self.status == "already_low":
            return True
        return (self.status == "projected" and self.days_left is not None
                and self.days_left <= MAX_HORIZON_DAYS)


def clean(samples) -> list[Sample]:
    """Sorted, de-duplicated by day (later reading wins), None-safe."""
    # Keep the whole reading, not just the share: a sample that has been cleaned
    # must still carry NDMI, the near/far contrast and the rain context, or every
    # predictor added in 016 would be discarded at the first step.
    by_day: dict[date, Sample] = {}
    for s in samples or ():
        try:
            if s is None or s.usable_pct is None:
                continue
            by_day[s.day] = s          # later reading for the same day wins
        except (AttributeError, TypeError, ValueError):
            continue
    return [by_day[d] for d in sorted(by_day)]


def fit(samples) -> tuple[float | None, float, int, int]:
    """Least-squares slope in percentage points per DAY.

    Returns (slope, r2, n, span_days). slope is None when a line cannot be fitted.
    r2 is 0..1 and is used only to decide how loudly we may speak.
    """
    return _line(clean(samples))


def _line(pts: list[Sample]) -> tuple[float | None, float, int, int]:
    """Least squares over ALREADY-CLEANED readings: (slope/day, r2, n, span_days).

    Shared by every fit below (usable%, NDMI) and by fit_dry, so a slope always
    means the same thing and is always computed the same way.
    """
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


def fit_dry(samples, max_rain_mm: float = DRY_WINDOW_MM
            ) -> tuple[float | None, float, int, int]:
    """Slope from DRY-PERIOD readings only: (slope/day, r2, n, span_days).

    The unconditional fit answers "what has this ring been doing for months", which
    is the wrong question in the weeks that matter: a green-up in the long rains is
    as real as the slide that follows it, and the two cancel into "stable". The rate
    that describes a collapse is the rate WHILE GROWTH HAS STOPPED, so this fits
    only the samples taken after 14 dry days - and returns slope None (not zero,
    not a guess) when too few readings qualify.
    """
    pts = [p for p in clean(samples)
           if p.rain_14d_mm is not None and p.rain_14d_mm <= max_rain_mm]
    if len(pts) < DRY_MIN_SAMPLES:
        return None, 0.0, len(pts), 0
    return _line(pts)


@dataclass(frozen=True)
class Pressure:
    """The SHAPE of the ring: what the near/far contrast can and cannot say.

    Grazing is heaviest at the water point, so the ground a herd walks over every
    day is the first to lose its grass and the last to recover. Comparing it with
    the OUTER ring removes the season: a dry spell thins both zones together, while
    too many animals for too long opens a gap between them.

    The label is about SPACE, never about a number of animals. We do not have
    reliable herd sizes (registration is slow) and inferring one from a satellite
    picture would be a guess dressed as a measurement. Wide gap AND still widening
    = grazing pressure happening now; wide gap but not widening = the old scar
    every water point carries (grazing happened, it is not what moved this week).
    """
    near_pct: float | None = None
    far_pct: float | None = None
    gap_pp: float | None = None          # far - near: positive = better away from water
    gap_change_pp: float | None = None   # how much that gap widened since the oldest
    label: str = "unknown"               # grazing_recent | grazing_legacy | even | unknown


def pressure(samples) -> Pressure:
    """Near-water vs outer-ring contrast in the stored readings."""
    pts = [p for p in clean(samples)
           if p.near_pct is not None and p.far_pct is not None]
    if not pts:
        return Pressure()
    last, first = pts[-1], pts[0]
    gap = float(last.far_pct) - float(last.near_pct)
    change = None
    if (last.day - first.day).days >= CAUSE_GAP_MIN_SPAN_DAYS:
        change = gap - (float(first.far_pct) - float(first.near_pct))
    if gap < CAUSE_GAP_PP:
        label = "even"          # thinned everywhere: the season, not the herd
    elif change is not None and change >= CAUSE_GAP_CHANGE_PP:
        label = "grazing_recent"
    else:
        label = "grazing_legacy"
    return Pressure(near_pct=round(float(last.near_pct), 1),
                    far_pct=round(float(last.far_pct), 1),
                    gap_pp=round(gap, 1),
                    gap_change_pp=None if change is None else round(change, 1),
                    label=label)


def cause_of(pres: Pressure) -> str:
    """season | grazing | unknown - only what the ring's shape can support."""
    if pres.label in ("grazing_recent", "grazing_legacy"):
        return "grazing"
    return "season" if pres.label == "even" else "unknown"


def is_curing(samples, slope_pp_week: float | None = None) -> bool:
    """Is the grass drying BEFORE the shares have moved? (a warning, never a date)

    NDMI says how much water is left in the grass, and it falls two to four weeks
    before a pixel stops being classed as dry forage - the difference between a
    smoke alarm and predicting the fire from the ashes. It is reported as a
    SENTENCE while usable% is still flat, and it never produces a date of its own,
    because the measurement a date is made from has not moved yet.
    """
    if slope_pp_week is not None and slope_pp_week < -NOISE_PP_PER_WEEK:
        return False        # already declining: the share itself is the signal
    pts = [p for p in clean(samples) if p.ndmi is not None]
    if len(pts) < NDMI_MIN_SAMPLES:
        return False
    slope, _r2, n, span = _line([Sample(day=p.day, usable_pct=p.ndmi) for p in pts])
    if slope is None or n < NDMI_MIN_SAMPLES or span < MIN_SPAN_DAYS:
        return False
    return slope * 7.0 <= NDMI_CURING_PER_WEEK


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

    # Computed once, so EVERY branch carries the same context: a ring that is
    # already spent still has a cause worth telling him, and a flat ring still has
    # a curing warning worth keeping.
    pres = pressure(pts)
    context = dict(cause=cause_of(pres), gap_pp=pres.gap_pp)
    slope, r2, n, span = fit(pts)
    per_week = None if slope is None else slope * 7.0
    context["curing"] = is_curing(pts, per_week)

    if usable_now is not None and usable_now <= runout_pct:
        # The ring is already spent: today's state is the answer, and it is more
        # urgent than any projection.
        rain_recovers = (rain_mm is not None and rain_mm >= RAIN_MAY_RECOVER_MM)
        return Depletion(status="already_low", usable_pct=usable_now, days_left=0,
                         days_left_lo=0, days_left_hi=0, samples=len(pts),
                         span_days=0, confidence="high",
                         rain_may_recover=rain_recovers, rain_onset=rain_onset,
                         **context)

    if slope is None or n < MIN_SAMPLES or span < MIN_SPAN_DAYS:
        # Honest start: we are still measuring. "I do not know yet" is the only
        # sentence that keeps the next answer believable.
        return Depletion(status="no_history", usable_pct=usable_now,
                         samples=n, span_days=span, confidence="low",
                         rain_onset=rain_onset, **context)

    # THE DRY-PERIOD RATE. Only trusted while the ring is actually IN a dry spell
    # (the newest reading was taken after 14 dry days): a rate measured during last
    # season's slide says nothing about a ring that is green again today.
    dry_slope, dry_r2, dry_n, dry_span = fit_dry(pts)
    dry_week = None if dry_slope is None else dry_slope * 7.0
    latest_dry = (pts[-1].rain_14d_mm is not None
                  and pts[-1].rain_14d_mm <= DRY_WINDOW_MM)
    rate_week, rate_source = per_week, "all"
    if dry_week is not None and dry_week < per_week and latest_dry:
        rate_week, rate_source = dry_week, "dry"
    extra = dict(context, rate_source=rate_source,
                 dry_slope_pp_week=None if dry_week is None else round(dry_week, 1))
    # How loudly we may speak about the rate we actually used: the dry-period fit
    # has its own sample count and fit quality, and must not borrow the long one's.
    cn, cspan, cr2 = ((dry_n, dry_span, dry_r2) if rate_source == "dry"
                      else (n, span, r2))

    if rate_week >= -NOISE_PP_PER_WEEK:
        # Flat or greening: there is no date to give, and inventing one would be
        # the fastest way to lose a herder who walks out and finds grass.
        return Depletion(status="stable", usable_pct=usable_now,
                         slope_pp_week=round(per_week, 1), samples=n, span_days=span,
                         confidence="moderate" if r2 >= 0.3 else "low",
                         rain_onset=rain_onset, **extra)

    days = (usable_now - runout_pct) / abs(rate_week / 7.0)
    if days > horizon_days:
        return Depletion(status="beyond_horizon", usable_pct=usable_now,
                         slope_pp_week=round(rate_week, 1), samples=n, span_days=span,
                         confidence=_confidence(cn, cspan, cr2),
                         rain_onset=rain_onset, **extra)

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
                     slope_pp_week=round(rate_week, 1), days_left=best,
                     days_left_lo=lo, days_left_hi=hi, samples=n, span_days=span,
                     confidence=_confidence(cn, cspan, cr2),
                     rain_may_recover=rain_recovers, rain_onset=rain_onset,
                     **extra)


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


def _why(dep: Depletion, sw: bool) -> str:
    """One sentence on WHY the grass is going, or "" when we cannot say.

    Two different claims, kept separate on purpose:
      * grazing - the ring is thinner near the water than at its edge, which the
        satellite measures directly and a head count is NOT needed for;
      * season  - it is thinner everywhere, which is what a dry spell looks like
        and is not evidence against his herd.
    Never a number of animals, and never a certainty: the verb is "suggests".
    """
    if dep.cause == "grazing":
        return (" Eneo la karibu na maji lina malisho machache kuliko la mbali — "
                "hii inaonyesha mifugo inakula sana karibu na maji." if sw else
                " The ground near the water has less grass than the outer ring — "
                "that points to heavy grazing close to the water.")
    if dep.cause == "season":
        return (" Upungufu uko kote sawa sawa — inaonyesha hali ya msimu." if sw else
                " The thinning is even across the whole ring — that is what a dry "
                "season looks like.")
    return ""


def _curing_note(dep: Depletion, sw: bool) -> str:
    """The smoke alarm: grass drying BEFORE the shares moved. Never a date."""
    if not dep.curing:
        return ""
    return (" Nyasi bado zinaonekana, lakini zinaanza kukauka — hali inaweza "
            "kubadilika hivi karibuni." if sw else
            " The grass still shows, but it is starting to dry out — this may "
            "change soon.")


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
        return body + conf + "." + _curing_note(dep, sw)

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
    parts = [head, line(dep, lang), _why(dep, sw)]
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
    "DRY_WINDOW_MM", "DRY_MIN_SAMPLES", "CAUSE_GAP_PP", "CAUSE_GAP_CHANGE_PP",
    "NDMI_CURING_PER_WEEK",
    "Sample", "Depletion", "Pressure",
    "clean", "fit", "fit_dry", "pressure", "cause_of", "is_curing",
    "forecast", "line", "alert_message",
]
