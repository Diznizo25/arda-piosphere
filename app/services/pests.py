"""Pest and parasite risk windows.

Why pests instead of disease: the environment-to-pest link is (a) well
established, (b) directly observable by the herder himself — he can SEE ticks and
pale eyelids — and (c) the action is a look, not a treatment. If our window is
wrong, he has wasted five minutes checking his animals; he has not been told his
animals are sick, has not been sent to buy a drug, and we have not contradicted
the county vet. That is the safest useful prediction we can ship, and it targets
production loss (weight and milk) rather than an outbreak.

THE HARD BOUNDARY (enforced by scripts/test_pests.py):
    We name the CONDITIONS and tell the herder WHERE TO LOOK on the animal.
    We never diagnose. We never name a drug or a dose. We never tell him to
    spend money or to treat. If he finds something, we route him to the vet.

How the rules work: every window is a small, transparent scorecard over the
signals we already store (rain, soil moisture, temperature, humidity, NDMI from
the satellite COG, plus what herders have told us). No machine learning, no black
box — so every sentence we send can be traced back to a number, and a herder can
argue with us. Thresholds are module constants with the biology written next to
them; they are defaults to be tuned against the observation loop
(pest_observations), not truths.

Pure functions only (no DB, no network) so this is unit-testable anywhere.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

log = logging.getLogger(__name__)

TIER_QUIET = "quiet"
TIER_WATCH = "watch"
TIER_RISING = "rising"
TIER_HIGH = "high"

TIER_ORDER = {TIER_QUIET: 0, TIER_WATCH: 1, TIER_RISING: 2, TIER_HIGH: 3}

TIER_LABEL_SWA = {
    TIER_QUIET: "hakuna tahadhari",
    TIER_WATCH: "kuwa makini",
    TIER_RISING: "hatari inaongezeka",
    TIER_HIGH: "hatari ni juu",
}
TIER_LABEL_ENG = {
    TIER_QUIET: "no warning",
    TIER_WATCH: "keep an eye out",
    TIER_RISING: "risk is rising",
    TIER_HIGH: "risk is high",
}

# --- thresholds -------------------------------------------------------------
# Each one carries its reasoning, because a threshold nobody can explain is a
# threshold nobody should trust.

# A day counts as "wet" from this much rain: below ~1 mm the ground stays dry
# and neither tick questing nor larval development is helped.
WET_DAY_MM = 1.0

# TICKS: larvae/nymphs quest in numbers days to weeks after rain, and survival
# needs humid vegetation. Warming is not required (they are active across a wide
# band) but extreme heat dries them out.
TICK_RAIN_7D_MM = 10.0        # a real wetting of the pasture, not a shower
TICK_SOIL_MOISTURE = 0.15     # volumetric fraction, 0-7 cm (Open-Meteo units)
TICK_TEMP_MIN_C = 15.0
TICK_TEMP_MAX_C = 32.0
TICK_NDMI = -0.02             # canopy moisture; PROVISIONAL, needs calibration

# WORMS (incl. Haemonchus): eggs hatch and larvae develop with warmth AND
# moisture; L3 survive longest on damp shaded ground, and dry heat clears them.
WORM_RAIN_14D_MM = 20.0       # sustained wetting, not one storm
WORM_MAX_DAYS_SINCE_WET = 10  # after this the pasture challenge has faded
WORM_TEMP_MIN_C = 18.0
WORM_SOIL_MOISTURE = 0.15

# FLIES / WOUND MYIASIS: warmth and humidity, plus any wound at all.
FLY_RAIN_7D_MM = 5.0
FLY_TEMP_MIN_C = 20.0
FLY_HUMIDITY_PCT = 60.0

# HOOVES: standing on wet ground for consecutive days.
HOOF_WET_DAYS = 3
HOOF_SOIL_MOISTURE = 0.20

PEST_KEYS = ("ticks", "worms", "flies", "hooves")

# Fire a weekly line in the advisory from this tier up.
ADVISORY_TIER = TIER_RISING


@dataclass(frozen=True)
class PestInputs:
    """The signals a window is computed from (all optional except the rain)."""
    rain_7d_mm: float = 0.0
    rain_14d_mm: float = 0.0
    days_since_wet: Optional[int] = None
    wet_days_streak: int = 0
    soil_moisture: Optional[float] = None
    temp_max_c: Optional[float] = None
    humidity: Optional[float] = None
    ndmi: Optional[float] = None
    # How many days of rain data we actually have. Needed to tell "we have no data"
    # apart from "we have data and it never rained", which are different answers.
    days_with_data: int = 0


def build_inputs(rain: Sequence[float], *, soil_moisture: Optional[float] = None,
                 temp_max_c: Optional[float] = None,
                 humidity: Optional[float] = None,
                 ndmi: Optional[float] = None) -> PestInputs:
    """Turn a daily rain series (oldest first) into window inputs.

    Only the last 14 days matter, so a shorter series simply means fewer signals
    — never a guess. `days_since_wet` counts backwards from the last observed
    day, which is the honest way to read it: we know the rain as of that day.
    """
    days = [float(x or 0.0) for x in (rain or [])]
    last14 = days[-14:]
    last7 = days[-7:]
    days_since_wet: Optional[int] = None
    for i, mm in enumerate(reversed(days)):
        if mm >= WET_DAY_MM:
            days_since_wet = i
            break
    streak = 0
    for mm in reversed(days):
        if mm >= WET_DAY_MM:
            streak += 1
        else:
            break
    return PestInputs(
        rain_7d_mm=round(sum(last7), 1),
        rain_14d_mm=round(sum(last14), 1),
        days_since_wet=days_since_wet,
        wet_days_streak=streak,
        soil_moisture=soil_moisture,
        temp_max_c=temp_max_c,
        humidity=humidity,
        ndmi=ndmi,
        days_with_data=len(days),
    )


def _tier(score: int, available: int) -> str:
    """Score -> tier. Deliberately conservative.

    A single available signal can never raise a window past 'watch': with the
    ground sensors we have, one number agreeing with itself is not evidence, and
    a false alarm costs a household real money in the field even when our advice
    is only 'go and look'.
    """
    if available == 0 or score == 0:
        return TIER_QUIET
    if available < 2:
        return TIER_WATCH
    ratio = score / available
    if ratio >= 1.0:
        return TIER_HIGH
    if ratio >= 0.5:
        return TIER_RISING
    return TIER_WATCH


@dataclass(frozen=True)
class PestWindow:
    """One pest/parasite window: a tier, the numbers behind it, and why."""
    key: str
    tier: str
    score: int
    signals_available: int
    evidence: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def rank(self) -> int:
        return TIER_ORDER.get(self.tier, 0)

    def reason_lines(self, lang: str = "swa") -> list[str]:
        i = 0 if lang in ("swa", "swahili") else 1
        return [e[i] for e in self.evidence]


def tick_window(inp: PestInputs) -> PestWindow:
    """Tick activity window: recent wetting + moist vegetation + workable warmth."""
    evidence: list[tuple[str, str]] = []
    available = 0
    score = 0

    # GATE: no moisture, no tick window. In the ASALs warmth is the background
    # state all year, so heat alone must never raise anything — the moisture is
    # what decides whether larvae survive and quest.
    gate = (inp.rain_7d_mm >= TICK_RAIN_7D_MM
            or (inp.soil_moisture is not None
                and inp.soil_moisture >= TICK_SOIL_MOISTURE)
            or (inp.ndmi is not None and inp.ndmi >= TICK_NDMI))

    available += 1
    if inp.rain_7d_mm >= TICK_RAIN_7D_MM:
        score += 1
        evidence.append((
            f"mvua ya {inp.rain_7d_mm:g} mm katika siku 7 zilizopita",
            f"{inp.rain_7d_mm:g} mm of rain in the last 7 days"))
    if inp.soil_moisture is not None:
        available += 1
        if inp.soil_moisture >= TICK_SOIL_MOISTURE:
            score += 1
            evidence.append(("unyevu wa udongo ni juu",
                             "soil moisture is high"))
    if inp.temp_max_c is not None:
        available += 1
        if TICK_TEMP_MIN_C <= inp.temp_max_c <= TICK_TEMP_MAX_C:
            score += 1
            evidence.append(("joto ni la kati, si joto kali",
                             "temperatures are in the tick activity band"))
    if inp.ndmi is not None:
        available += 1
        if inp.ndmi >= TICK_NDMI:
            score += 1
            evidence.append(("malisho yana unyevu (picha ya satelaiti)",
                             "vegetation is moist (satellite image)"))

    if not gate:
        return PestWindow("ticks", TIER_QUIET, 0, available)
    return PestWindow("ticks", _tier(score, available), score, available,
                      tuple(evidence))


def worm_window(inp: PestInputs) -> PestWindow:
    """Pasture worm challenge: sustained rain, warmth, and larvae still alive."""
    evidence: list[tuple[str, str]] = []
    available = 0
    score = 0

    # GATE: larvae need a wet pasture. Warm and dry means no challenge, however
    # pleasant the temperature looks.
    gate = (inp.rain_14d_mm >= WORM_RAIN_14D_MM
            or (inp.soil_moisture is not None
                and inp.soil_moisture >= WORM_SOIL_MOISTURE))

    available += 1
    if inp.rain_14d_mm >= WORM_RAIN_14D_MM:
        score += 1
        evidence.append((
            f"mvua ya {inp.rain_14d_mm:g} mm katika siku 14",
            f"{inp.rain_14d_mm:g} mm of rain over 14 days"))
    if inp.days_since_wet is not None:
        available += 1
        if inp.days_since_wet <= WORM_MAX_DAYS_SINCE_WET:
            score += 1
            n = inp.days_since_wet
            # "0 days ago" is not how anyone speaks: say today / yesterday.
            if n == 0:
                evidence.append(("mvua ilinyesha leo", "rain fell today"))
            elif n == 1:
                evidence.append(("mvua ilinyesha jana", "rain fell yesterday"))
            else:
                evidence.append((
                    f"mvua ya mwisho ilinyesha siku {n} zilizopita",
                    f"the last rain fell {n} days ago"))
    if inp.temp_max_c is not None:
        available += 1
        if inp.temp_max_c >= WORM_TEMP_MIN_C:
            score += 1
            evidence.append(("joto linatosha kwa mayai ya minyoo",
                             "temperatures are warm enough for worm larvae"))
    if inp.soil_moisture is not None:
        available += 1
        if inp.soil_moisture >= WORM_SOIL_MOISTURE:
            score += 1
            evidence.append(("udongo wa malisho ni wenye unyevu",
                             "pasture soil is damp"))

    if not gate:
        return PestWindow("worms", TIER_QUIET, 0, available)
    return PestWindow("worms", _tier(score, available), score, available,
                      tuple(evidence))


def fly_window(inp: PestInputs) -> PestWindow:
    """Flies and wound myiasis: warm, humid weather."""
    evidence: list[tuple[str, str]] = []
    available = 0
    score = 0

    # GATE: flies need damp air or wet ground. Warmth alone is a normal day here.
    gate = (inp.rain_7d_mm >= FLY_RAIN_7D_MM
            or (inp.humidity is not None and inp.humidity >= FLY_HUMIDITY_PCT))

    available += 1
    if inp.rain_7d_mm >= FLY_RAIN_7D_MM:
        score += 1
        evidence.append((f"mvua ya {inp.rain_7d_mm:g} mm katika siku 7",
                         f"{inp.rain_7d_mm:g} mm of rain in the last 7 days"))
    if inp.temp_max_c is not None:
        available += 1
        if inp.temp_max_c >= FLY_TEMP_MIN_C:
            score += 1
            evidence.append(("joto ni juu", "temperatures are warm"))
    if inp.humidity is not None:
        available += 1
        if inp.humidity >= FLY_HUMIDITY_PCT:
            score += 1
            evidence.append((f"unyevu wa hewa ni asilimia {inp.humidity:g}",
                             f"humidity is {inp.humidity:g} percent"))

    if not gate:
        return PestWindow("flies", TIER_QUIET, 0, available)
    return PestWindow("flies", _tier(score, available), score, available,
                      tuple(evidence))


def hoof_window(inp: PestInputs) -> PestWindow:
    """Hoof problems: animals standing on wet ground day after day."""
    evidence: list[tuple[str, str]] = []
    available = 0
    score = 0

    # GATE: soft hooves need water underfoot. Same reasoning as the others.
    gate = (inp.wet_days_streak >= HOOF_WET_DAYS
            or (inp.soil_moisture is not None
                and inp.soil_moisture >= HOOF_SOIL_MOISTURE))

    available += 1
    if inp.wet_days_streak >= HOOF_WET_DAYS:
        score += 1
        n = inp.wet_days_streak
        evidence.append((f"siku {n} mfululizo za mvua",
                         f"{n} wet days in a row"))
    if inp.soil_moisture is not None:
        available += 1
        if inp.soil_moisture >= HOOF_SOIL_MOISTURE:
            score += 1
            evidence.append(("udongo ni wa maji sana",
                             "the ground is very wet"))

    if not gate:
        return PestWindow("hooves", TIER_QUIET, 0, available)
    return PestWindow("hooves", _tier(score, available), score, available,
                      tuple(evidence))


@dataclass(frozen=True)
class PestOutlook:
    """All four windows for a place, plus the one we would talk about first."""
    windows: tuple[PestWindow, ...]
    # The inputs are carried along so a "nothing is up" answer can give the REASON
    # (how long since real rain) instead of a verdict nobody can use.
    inputs: Optional[PestInputs] = None

    @property
    def active(self) -> tuple[PestWindow, ...]:
        return tuple(w for w in self.windows if w.rank >= TIER_ORDER[TIER_WATCH])

    @property
    def alerting(self) -> tuple[PestWindow, ...]:
        return tuple(w for w in self.windows if w.rank >= TIER_ORDER[ADVISORY_TIER])

    @property
    def top(self) -> PestWindow | None:
        """The window a herder should hear about first (None when all quiet)."""
        active = self.active
        if not active:
            return None
        return max(active, key=lambda w: (w.rank, w.score))

    @property
    def highest_tier(self) -> str:
        return max((w.tier for w in self.windows), key=lambda t: TIER_ORDER[t],
                   default=TIER_QUIET)


def outlook(inp: PestInputs) -> PestOutlook:
    """Compute all four windows from the same inputs."""
    return PestOutlook((tick_window(inp), worm_window(inp), fly_window(inp),
                        hoof_window(inp)), inputs=inp)


# --- guidance content -------------------------------------------------------

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "pest_guidance.yaml"
_GUIDANCE_CACHE: dict | None = None

# Words that must never appear in anything we send a herder. This is the
# check-first boundary made mechanical: no drug names, no treatment verbs, no
# spending advice. scripts/test_pests.py asserts this over the rendered messages.
FORBIDDEN_WORDS = (
    "tibu", "tiba", "dawa", "chanjo", "kamua", "kuua wadudu", "ivermectin",
    "albendazole", "acaricide", "deworm", "drug", "dose", "inject", "vaccin",
    "kununua dawa", "spray",
)


def load_guidance(path: str | Path | None = None) -> dict:
    """The vet-reviewable content file (cached). Fail-open: an empty dict means
    the message loses its checklist but still names the condition and the tier."""
    global _GUIDANCE_CACHE
    if path is None and _GUIDANCE_CACHE is not None:
        return _GUIDANCE_CACHE
    p = Path(path) if path else CONFIG_PATH
    try:
        import yaml

        with open(p, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001
        log.exception("pest guidance load failed (fail-open, no checklist)")
        data = {}
    if path is None:
        _GUIDANCE_CACHE = data
    return data


def pest_guidance(key: str, lang: str = "swa", guidance: dict | None = None) -> dict:
    """One pest's wording in one language (empty strings when not written yet)."""
    g = guidance if guidance is not None else load_guidance()
    entry = ((g.get("pests") or {}).get(key)) or {}
    i = "swa" if lang in ("swa", "swahili") else "eng"
    return {
        "label": ((entry.get("label") or {}).get(i)) or key,
        "where_to_look": ((entry.get("where_to_look") or {}).get(i)) or [],
        "signs": ((entry.get("signs") or {}).get(i)) or [],
        "if_found": ((entry.get("if_found") or {}).get(i)) or "",
        "why": ((entry.get("why") or {}).get(i)) or "",
    }


def tier_label(tier: str, lang: str = "swa") -> str:
    table = TIER_LABEL_SWA if lang in ("swa", "swahili") else TIER_LABEL_ENG
    return table.get(tier, table[TIER_QUIET])


# --- messages ---------------------------------------------------------------

_OBSERVATION_QUESTION = {
    "ticks": {
        "swa": "Umeona kupe kwenye mifugo yako?",
        "eng": "Did you see ticks on your animals?",
    },
    "worms": {
        "swa": "Umekagua kope la jicho? Umeona rangi ya pinki au jeupe?",
        "eng": "Did you check the eyelid colour? Is it pale pink or white?",
    },
    "flies": {
        "swa": "Umeona vidonda vyenye inzi au mabuu?",
        "eng": "Did you see wounds with flies or maggots?",
    },
    "hooves": {
        "swa": "Umeona mnyama akichechemea au kwato yenye harufu?",
        "eng": "Did you see a limping animal or a hoof with a bad smell?",
    },
}


def observation_question(win: PestWindow, lang: str = "swa") -> str:
    """The one-tap question for the window we just described."""
    i = "swa" if lang in ("swa", "swahili") else "eng"
    q = (_OBSERVATION_QUESTION.get(win.key) or {}).get(i) or (
        "Umeangalia mifugo yako?" if i == "swa" else "Did you check your animals?")
    tail = "Jibu 1 kama ndio, 2 kama hapana." if i == "swa" else (
        "Reply 1 for yes, 2 for no.")
    return f"{q}\n{tail}"


def observation_for_digit(text: str) -> bool | None:
    """'1' = saw it, '2' = looked and saw nothing (None = not an answer)."""
    t = (text or "").strip().lower()
    if t == "1":
        return True
    if t == "2":
        return False
    return None


def thanks_for_observation(seen: bool, lang: str = "swa") -> str:
    """Reciprocity, and the honest reason a 'nothing' answer matters."""
    if lang in ("swa", "swahili"):
        if seen:
            return ("Asante! Taarifa yako inasaidia kuonyesha wapi kupe na minyoo "
                    "wapo.\nWasiliana na afisa wa mifugo au agrovet kwa ushauri.")
        return ("Asante kwa kuangalia! Jibu la 'hapana' linatusaidia kujua "
                "wapi hatari haipo.\nTutaendelea kufuatilia mvua na joto.")
    if seen:
        return ("Thank you! Your report helps show where the problem is.\n"
                "Speak to your vet or agrovet for advice.")
    return ("Thank you for looking! A 'no' answer helps us learn where the risk "
            "is not.\nWe will keep tracking rain and temperature.")


def headline(win: PestWindow, lang: str = "swa", guidance: dict | None = None) -> str:
    """The opening line for a window ("Kupe: hatari inaongezeka wiki hii.")."""
    g = pest_guidance(win.key, lang, guidance)
    if lang in ("swa", "swahili"):
        return f"{g['label']}: {tier_label(win.tier, lang)} wiki hii."
    return f"{g['label']}: {tier_label(win.tier, lang)} this week."


_TITLE = {"swa": "KUPE NA MINYOO", "eng": "TICKS AND WORMS"}
_VIGILANCE = {"swa": "Kuwa makini. Angalia mifugo yako.",
              "eng": "Be vigilant. Check your animals."}


def _reason_for_quiet(o: "PestOutlook", lang: str = "swa") -> str:
    """Why nothing is up — the part that makes a quiet answer worth reading."""
    inp = getattr(o, "inputs", None)
    days = inp.days_since_wet if inp is not None else None
    have = inp.days_with_data if inp is not None else 0
    sw = lang in ("swa", "swahili")
    if not have:
        return ("Hatuna data ya kutosha ya mvua na joto bado." if sw
                else "We do not have enough rain and temperature data yet.")
    if days is None:
        # We have the series and it never rained in it: a real dry spell, not a
        # data gap. Saying "no data" here would be a lie of the worst kind.
        return (f"Hakuna mvua hata moja katika siku {have} zilizopita, na udongo ni "
                f"mkavu." if sw
                else f"No rain at all in the last {have} days, and the ground is dry.")
    if days >= 7:
        return (f"Sababu ni siku {days} bila mvua ya maana, na udongo ni mkavu." if sw
                else f"Because there has been no real rain for {days} days and the "
                     f"ground is dry.")
    if days <= 1:
        return ("Mvua ya mwisho ilinyesha leo, lakini haitoshi bado kuongeza kupe." if sw
                else "The last rain fell today, but it is not enough yet to lift ticks.")
    return (f"Mvua ya mwisho ilinyesha siku {days} zilizopita, lakini haitoshi bado "
            f"kuongeza kupe." if sw
            else f"The last rain fell {days} days ago, but it is not enough yet to "
                 f"lift ticks.")


def quiet_message(o: "PestOutlook", lang: str = "swa") -> str:
    """The answer when no window is up: the REASON, plus when it will change.

    "Conditions are normal" is a verdict, not information — a herder who asked
    about ticks learns nothing from it and will not ask twice. Naming the reason
    (how long since real rain, dry ground) and the trigger ("when sustained rain
    arrives we will warn you") turns a non-event into something he can act on and
    tells him what the next message will mean.
    """
    i = "swa" if lang in ("swa", "swahili") else "eng"
    inp = getattr(o, "inputs", None)
    # With no data we cannot claim there is no warning: say what we know instead.
    if inp is None or not inp.days_with_data:
        if i == "swa":
            return (f"🔍 {_TITLE[i]}\n\nHatuna data ya mvua na joto ya eneo lako bado, "
                    f"hivyo siwezi kukupa tahadhari kwa uhakika.\n"
                    f"Huduma hii itaanza mara data ikiwadia.")
        return (f"🔍 {_TITLE[i]}\n\nWe do not have rain and temperature data for your "
                f"area yet, so I cannot give you a confident warning.\n"
                f"This service will start as soon as the data arrives.")
    trigger = ("Kupe na minyoo huongezeka mvua ya mfululizo ianzapo. "
               "Tutakutumia tahadhari wakati huo." if i == "swa" else
               "Ticks and worms rise with sustained rain. "
               "We will warn you when they do.")
    first = "Hakuna tahadhari sasa." if i == "swa" else "No warning right now."
    return f"🔍 {_TITLE[i]}\n\n{first}\n{_reason_for_quiet(o, lang)}\n\n{trigger}"


def message(o: PestOutlook, lang: str = "swa", guidance: dict | None = None,
            max_windows: int = 2) -> str:
    """The full check-first message for the windows that are up.

    Two windows at most: a herder can act on one look, and a message that lists
    everything gets skimmed. Above the windows we always give the numbers, so the
    herder can judge us instead of trusting us.
    """
    i = "swa" if lang in ("swa", "swahili") else "eng"
    active = o.active[:max_windows]
    if not active:
        return quiet_message(o, lang)

    blocks: list[str] = []
    for win in active:
        g = pest_guidance(win.key, lang, guidance)
        lines = [headline(win, lang, guidance)]
        reasons = win.reason_lines(lang)
        if reasons:
            # Three reasons at most: the herder needs enough to judge us, not a
            # data dump. The full evidence stays in /dev/pest for the team.
            prefix = "Sababu ni " if i == "swa" else "Based on "
            lines.append(f"{prefix}{', '.join(reasons[:3])}.")
        if g["where_to_look"]:
            lines.append(_VIGILANCE[i])
            lines.extend(f"• {item}" for item in g["where_to_look"])
        if g["if_found"]:
            lines.append(g["if_found"])
        blocks.append("\n".join(lines))

    parts = [f"🔍 {_TITLE[i]}", "\n\n".join(blocks)]
    top = o.top
    if top is not None:
        parts.append(observation_question(top, lang))
    return "\n\n".join(parts)


def weekly_line(o: PestOutlook, lang: str = "swa",
                guidance: dict | None = None) -> str | None:
    """One line for the weekly note — None when no window is at 'rising' or above.

    Silence is a feature: if we wrote a pest line every week the herder would stop
    reading it, and the week it matters he would skim past it.
    """
    alerting = o.alerting
    if not alerting:
        return None
    win = max(alerting, key=lambda w: (w.rank, w.score))
    g = pest_guidance(win.key, lang, guidance)
    if lang in ("swa", "swahili"):
        return (f"🔍 {g['label']}: {tier_label(win.tier, lang)} wiki hii. "
                "Kuwa makini na uangalie mifugo yako.")
    return (f"🔍 {g['label']}: {tier_label(win.tier, lang)} this week. "
            "Be vigilant and check your animals.")


# --- DB-facing helpers (fail-open: no window is better than a wrong one) -----


def outlook_for_point(water_source_id: str, ndmi: float | None = None) -> PestOutlook | None:
    """The pest outlook for a water point from stored environment data.

    Reads only; the request path never calls a weather API (architecture rule #1).
    Returns None when there is no series at all, so callers simply add no pest
    line rather than inventing one.
    """
    try:
        from app.services import environment

        rows = environment.recent_series(water_source_id, days=90)
    except Exception:  # noqa: BLE001
        log.exception("pest outlook read failed for %s (non-fatal)", water_source_id)
        return None
    if not rows:
        return None

    rain = [float(r.get("rain_mm") or 0.0) for r in rows]
    soil = next((float(r["soil_moisture"]) for r in reversed(rows)
                 if r.get("soil_moisture") is not None), None)
    temps = [float(r["temperature_max_c"]) for r in rows[-14:]
             if r.get("temperature_max_c") is not None]
    humid = [float(r["humidity"]) for r in rows[-14:]
             if r.get("humidity") is not None]
    return outlook(build_inputs(
        rain,
        soil_moisture=soil,
        temp_max_c=(round(sum(temps) / len(temps), 1) if temps else None),
        humidity=(round(sum(humid) / len(humid), 1) if humid else None),
        ndmi=ndmi,
    ))


def record_observation(pastoralist_id: str | None, water_source_id: str | None,
                       pest_key: str, seen: bool, phone: str | None = None,
                       note: str | None = None) -> None:
    """Store what the herder saw — or looked for and did not see.

    This is the calibration loop, and the whole reason a herder's tap is worth
    more than our model: a "looked and saw nothing" answer is as valuable as a
    positive one, because it is what keeps our windows honest.
    """
    from app.db import get_pg_connection

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """insert into pest_observations
                       (phone, water_source_id, pest_key, seen, note)
                   values (%(phone)s, %(water_source_id)s, %(pest_key)s,
                           %(seen)s, %(note)s)""",
                {"phone": phone, "water_source_id": water_source_id,
                 "pest_key": pest_key, "seen": bool(seen), "note": note},
            )
        conn.commit()
    log.info("Pest observation key=%s seen=%s water_source_id=%s",
             pest_key, seen, water_source_id)


__all__ = [
    "TIER_QUIET", "TIER_WATCH", "TIER_RISING", "TIER_HIGH", "TIER_ORDER",
    "PEST_KEYS", "ADVISORY_TIER", "FORBIDDEN_WORDS", "PestInputs", "PestWindow",
    "PestOutlook", "build_inputs", "tick_window", "worm_window", "fly_window",
    "hoof_window", "outlook", "outlook_for_point", "load_guidance",
    "pest_guidance", "tier_label", "headline", "message", "weekly_line",
    "quiet_message", "observation_question", "observation_for_digit",
    "thanks_for_observation", "record_observation",
]




