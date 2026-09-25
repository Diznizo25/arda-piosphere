"""
Rain outlook logic: turning rainfall facts into herder-meaningful statements.

This module is PURE (no DB, no network, no GEE) exactly like water_status.py, so
the honesty rules below can be unit-tested in isolation. The rules are the whole
point of the file:

  1. A forecast is never stated as a fact. Anything beyond the ~7-day window gets
     an explicit confidence label, and the message always says it is a forecast
     (utabiri), because a herder deciding whether to move 200 cattle cannot act on
     a confident-sounding guess.
  2. "Dry" is always relative to what is normal for THAT place and THAT month -
     that is what rainfall_climatology is for. 20 mm in a month whose normal is
     5 mm is a wet month; 20 mm in a month whose normal is 80 mm is a drought.
  3. Every outlook ends with something to DO, and the caller can always fall back
     to no rain line at all (fail-open) when the data is missing.

Skill levels (why the thresholds are what they are): rain forecast skill in
equatorial East Africa is useful 1-7 days out, marginal 8-16 days out, and poor
for a whole season. We therefore label horizons and never turn a seasonal
outlook into a date.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

# A day counts as "wet" (wets the surface, starts grass response) at this much rain.
WET_DAY_MM = 1.0
# Rain needed over a short window to count as an onset of the rains.
ONSET_WINDOW_DAYS = 3
ONSET_WINDOW_MM = 5.0
# Beyond this the forecast gets the "moderate" label instead of "high".
HIGH_CONFIDENCE_DAYS = 7
# Below this climatological normal (mm per 30-day window) a percentage deficit is
# NOISE, not a drought: September at Lengwenyi normally brings ~1.7 mm, so "98%
# below normal" there means "0.1 mm instead of 1.7 mm" - the normal dry season.
# Crying drought in a dry month would destroy trust in the days when we are right,
# so a deficit against a negligible normal is labelled dry_season, never dry.
MEANINGFUL_NORMAL_MM = 10.0

CONFIDENCE_SW = {"high": "ya kuaminika", "moderate": "makadirio"}
CONFIDENCE_EN = {"high": "fairly reliable", "moderate": "a rough estimate"}


@dataclass
class RainOutlook:
    """Everything the rain line needs, all of it already computed from stored data."""

    dry_spell_days: int | None = None          # days since last wet day
    rain_30d_mm: float | None = None
    normal_30d_mm: float | None = None
    deficit_pct: float | None = None           # negative = drier than normal
    forecast_total_mm: float | None = None     # over the whole forecast horizon
    onset_date: date | None = None             # first 3-day wet window
    horizon_days: int | None = None
    generated_on: date | None = None
    forecast_age_days: int | None = None       # how old the cached forecast is
    confidence: str = "moderate"               # high | moderate
    has_forecast: bool = False
    soil_moisture: float | None = None
    extras: dict = field(default_factory=dict)


def dry_spell_days(rain_series: list[float], wet_day_mm: float = WET_DAY_MM) -> int:
    """Days since the last wet day, counting backwards from the end of the series.

    Returns the length of the series when nothing in it was wet (i.e. "at least
    this many days"), so a caller can say "no real rain for at least 30 days"
    instead of implying an exact figure.
    """
    if not rain_series:
        return 0
    count = 0
    for mm in reversed(rain_series):
        if (mm or 0.0) >= wet_day_mm:
            break
        count += 1
    return count


def rain_deficit_pct(observed_mm: float | None, normal_mm: float | None) -> float | None:
    """Signed percent of normal: -60 means 60% drier than normal for the window.

    Returns None when there is no usable normal (we never divide by a guess).
    """
    if observed_mm is None or not normal_mm or normal_mm <= 0:
        return None
    return round(((observed_mm - normal_mm) / normal_mm) * 100.0, 1)


def normal_for_window(climatology: dict[int, float], start: date, days: int,
                      days_in_month) -> float | None:
    """Climatological rainfall (mm) for an arbitrary date window.

    `climatology` maps month -> normal mm. `days_in_month` is injected (a callable
    year, month -> number of days) so this stays pure and testable without a
    date library. Months are weighted by how many of their days fall inside the
    window.
    """
    if not climatology or days <= 0:
        return None
    total = 0.0
    cursor = start
    counted = 0
    saw_a_month = False
    while counted < days:
        dim = days_in_month(cursor.year, cursor.month)
        take = min(dim - cursor.day + 1, days - counted)
        normal = climatology.get(cursor.month)
        if normal is not None:
            total += normal * (take / dim)
            saw_a_month = True
        counted += take
        cursor = cursor + timedelta(days=take)
    return round(total, 2) if saw_a_month else None


def forecast_onset(daily: list[dict], window_mm: float = ONSET_WINDOW_MM,
                   window_days: int = ONSET_WINDOW_DAYS,
                   wet_day_mm: float = WET_DAY_MM) -> date | None:
    """First date on which a `window_days` spell totals >= `window_mm`.

    This is "the rains are starting", not "it will rain on this day". The date
    returned is the first day *within* that qualifying spell that actually has
    rain (the leading day may be a trace), so the answer matches what a herder
    would see on the ground. None = no onset inside the horizon.
    """
    dates: list[date] = []
    amounts: list[float] = []
    for row in daily or []:
        raw = row.get("date")
        if not raw:
            continue
        try:
            d = date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
        dates.append(d)
        amounts.append(float(row.get("rain_mm") or 0.0))
    for i in range(max(0, len(amounts) - window_days + 1)):
        spell = amounts[i:i + window_days]
        if sum(spell) < window_mm:
            continue
        for offset, mm in enumerate(spell):
            if mm >= wet_day_mm:
                return dates[i + offset]
        return dates[i]
    return None

def build_outlook(*, recent_rain: list[float], climatology: dict[int, float],
                  daily_forecast: list[dict], window_days: int,
                  as_of: date, forecast_generated_on: date | None = None,
                  days_in_month=None, soil_moisture: float | None = None) -> RainOutlook:
    """Assemble a RainOutlook from stored facts. Missing pieces stay None — the
    formatter decides what can honestly be said with what we have."""
    if days_in_month is None:
        from calendar import monthrange

        def days_in_month(y, m):
            return monthrange(y, m)[1]

    recent = [float(x or 0.0) for x in (recent_rain or [])][-window_days:]
    observed = round(sum(recent), 2) if recent else None
    start = as_of - timedelta(days=max(0, len(recent) - 1))
    normal = (normal_for_window(climatology, start, len(recent), days_in_month)
              if recent else None)

    horizon = len(daily_forecast or [])
    total_mm = (round(sum(float(r.get("rain_mm") or 0.0) for r in daily_forecast), 2)
                if daily_forecast else None)
    onset = forecast_onset(daily_forecast)
    age = (as_of - forecast_generated_on).days if forecast_generated_on else None
    # A cached forecast older than its own horizon is worthless: ignore it rather
    # than tell a herder about rain that was predicted for three weeks ago.
    fresh = age is not None and 0 <= age <= horizon

    return RainOutlook(
        dry_spell_days=dry_spell_days(recent) if recent else None,
        rain_30d_mm=observed,
        normal_30d_mm=normal,
        deficit_pct=rain_deficit_pct(observed, normal),
        forecast_total_mm=total_mm if fresh else None,
        onset_date=onset if fresh else None,
        horizon_days=horizon if fresh else None,
        generated_on=forecast_generated_on,
        forecast_age_days=age,
        confidence=("high" if (onset and (onset - as_of).days <= HIGH_CONFIDENCE_DAYS)
                    else "moderate"),
        has_forecast=fresh,
        soil_moisture=soil_moisture,
        extras={"recent_days": len(recent)},
    )


def season_note(o: RainOutlook, lang: str = "swahili") -> str:
    """Herder-worded description of the season bucket (never the raw enum).

    The internal bucket names (dry_season, very_dry, ...) must never reach a
    herder: they are code. Callers put this sentence in the facts bundle instead.
    """
    sev = outlook_severity(o)
    return (_SEV_SW if lang != "english" else _SEV_EN)[sev]


def outlook_severity(o: RainOutlook) -> str:
    """normal | dry | very_dry | dry_season — the bucket we are willing to name.

    Deliberately conservative, twice over:
      1. Only a clear deficit (>=30% / >=60% drier than the normal for the same
         window) is called dry at all.
      2. A deficit measured against a negligible normal is NOT a drought. In a
         month whose 30-day normal is a couple of millimetres, "98% below normal"
         describes the ordinary dry season, so it is reported as dry_season.
    """
    if o is None or o.deficit_pct is None:
        return "normal"
    if o.normal_30d_mm is not None and o.normal_30d_mm < MEANINGFUL_NORMAL_MM:
        return "dry_season"
    if o.deficit_pct <= -60:
        return "very_dry"
    if o.deficit_pct <= -30:
        return "dry"
    return "normal"



# --- herder-facing wording ---------------------------------------------------
# Kept here rather than in i18n.py because these strings are inseparable from the
# skill rules above: changing a threshold must force you to re-read the sentence.

_SEV_SW = {
    "normal": "mvua iko karibu na kawaida ya msimu huu",
    "dry": "mvua ni pungufu kidogo ya kawaida ya msimu huu",
    "very_dry": "mvua ni pungufu sana ya kawaida ya msimu huu",
    # A small deficit against a tiny normal is the ordinary dry season, not a
    # drought - the wording must not frighten a herder who knows this already.
    "dry_season": "mvua ni kidogo, lakini huu ni msimu wa kiangazi wa kawaida",
}
_SEV_EN = {
    "normal": "rain is near normal for this season",
    "dry": "rain is a bit below normal for this season",
    "very_dry": "rain is far below normal for this season",
    "dry_season": "rain is minimal, but this is the normal dry season",
}


def rain_line(o: RainOutlook | None, lang: str = "swahili") -> str | None:
    """One short, honest line for the advisory. None when we have nothing to say.

    Written as complete sentences with NO leading label and NO parentheticals: this
    text is also spoken by the voice note, and "(makadirio)" disappears when spoken,
    which would silently drop the uncertainty from audio only. Uncertainty is a
    sentence here, so it survives both channels.
    """
    if o is None:
        return None
    sw = lang != "english"
    parts: list[str] = []

    if o.dry_spell_days is not None:
        if o.dry_spell_days == 0:
            parts.append("Mvua ilinyesha leo." if sw else "It rained today.")
        else:
            parts.append((f"Siku {o.dry_spell_days} zimepita bila mvua ya maana." if sw
                          else f"No real rain for {o.dry_spell_days} days."))
    if o.deficit_pct is not None:
        sev = outlook_severity(o)
        sev_text = _SEV_SW[sev] if sw else _SEV_EN[sev]
        parts.append(sev_text[:1].upper() + sev_text[1:] + ".")

    if o.has_forecast and o.horizon_days:
        if o.onset_date:
            days = max(0, (o.onset_date - (o.generated_on or o.onset_date)).days)
            if o.confidence == "high":
                parts.append(
                    (f"Mvua inaweza kuanza baada ya siku {days}. Huu ni utabiri "
                     f"wa siku chache, wa kuaminika." if sw
                     else f"Rain may start in about {days} days. This is a "
                          f"short-range forecast, fairly reliable."))
            else:
                parts.append(
                    (f"Katika siku {o.horizon_days} zijazo kuna ishara za mvua, "
                     f"lakini si ya uhakika. Huu ni makadirio." if sw
                     else f"Over the next {o.horizon_days} days there are signs of "
                          f"rain, but it is not certain. This is an estimate."))
        elif outlook_severity(o) == "dry_season":
            parts.append(
                (f"Katika siku {o.horizon_days} zijazo hakuna mvua inayotarajiwa. "
                 f"Huu ni makadirio." if sw
                 else f"No rain is expected over the next {o.horizon_days} days. "
                      f"This is an estimate."))
        else:
            parts.append(
                (f"Katika siku {o.horizon_days} zijazo mvua inayotarajiwa ni "
                 f"milimita {o.forecast_total_mm:.0f} pekee. Huu ni makadirio." if sw
                 else f"Only {o.forecast_total_mm:.0f} mm of rain is expected over "
                      f"the next {o.horizon_days} days. This is an estimate."))
    elif o.forecast_age_days is not None:
        parts.append("Utabiri wa mvua haupatikani kwa sasa." if sw
                     else "No rain forecast is available right now.")

    if not parts:
        return None
    return " ".join(parts)


def place_rain_line(own_mm: float | None, places: list[dict],
                    lang: str = "swahili") -> str | None:
    """Where the rain did and did not fall, in place names — never a drawn band.

    Why words instead of a shaded map: our rain data is a forecast/observation at a
    POINT, not a picture of the ground. A band would claim spatial precision we do
    not have and would look authoritative when wrong, and it would be staler than
    this text (the data refreshes twice a day). Pastoralists also speak in places,
    not colours: "mvua ilinyesha upande wa Kipsing".

    `places` is a list of {name, rain_7d_mm, direction_swa} for the nearest points
    with data. Returns None when there is nothing worth saying (including when the
    difference between here and there is trivial — a sentence that says nothing is
    noise the herder learns to ignore).

    Units are written "3 mm" (not "mm 3") because that is the form the voice pass
    turns into "milimita tatu" for the spoken version.
    """
    sw = lang != "english"
    named = [p for p in (places or []) if p.get("name")]
    if own_mm is None or not named:
        return None

    wettest = max(named, key=lambda p: float(p.get("rain_7d_mm") or 0.0))
    w_mm = float(wettest.get("rain_7d_mm") or 0.0)
    # Say nothing when nobody nearby got meaningfully more rain than here.
    if w_mm < own_mm + 5.0:
        return None

    direction = wettest.get("direction_swa")
    if sw:
        tail = f" upande wa {direction}" if direction else ""
        return (f"Mvua ya siku 7. Kwako {own_mm:.1f} mm. "
                f"{wettest['name']} {w_mm:.1f} mm{tail}.")
    tail = f" to the {direction}" if direction else ""
    return (f"Rain over 7 days. Here {own_mm:.1f} mm. "
            f"{wettest['name']} {w_mm:.1f} mm{tail}.")



def mvua_message(o: RainOutlook | None, place: str | None = None,
                 lang: str = "swahili") -> str:
    """The fuller answer for the 'mvua' service request.

    Same honesty rules as rain_line, with the numbers spelled out and an action
    attached. Written as sentences rather than label:value rows because the same
    text is spoken aloud by a voice note, and spoken parentheticals vanish.
    """
    sw = lang != "english"
    if o is None:
        return ("Samahani, hatuna data ya mvua kwa eneo hili bado. Tuma eneo lako "
                "tena baadaye." if sw else
                "Sorry, we do not have rain data for this area yet. Send your "
                "location again later.")

    where = f" ({place})" if place else ""
    lines: list[str] = [("🌧 MVUA" if sw else "🌧 RAIN") + where]

    if o.dry_spell_days is not None:
        lines.append((f"Siku {o.dry_spell_days} zimepita bila mvua ya maana." if sw
                      else f"No real rain for {o.dry_spell_days} days."))
    if o.deficit_pct is not None and o.normal_30d_mm:
        recent_days = o.extras.get("recent_days", 30)
        lines.append(
            (f"Katika siku {recent_days} zilizopita mvua ilikuwa milimita "
             f"{o.rain_30d_mm:.0f}, wakati kawaida ni milimita "
             f"{o.normal_30d_mm:.0f}." if sw else
             f"Over the past {recent_days} days there was {o.rain_30d_mm:.0f} mm of "
             f"rain, when the normal is {o.normal_30d_mm:.0f} mm."))
        sev = outlook_severity(o)
        sev_text = _SEV_SW[sev] if sw else _SEV_EN[sev]
        lines.append(sev_text[:1].upper() + sev_text[1:] + ".")
    if o.has_forecast and o.horizon_days:
        if o.onset_date:
            days = max(0, (o.onset_date - (o.generated_on or o.onset_date)).days)
            lines.append(
                (f"Mvua inaweza kuanza baada ya siku {days}. Huu ni utabiri, si "
                 f"uhakika." if sw else
                 f"Rain may start in about {days} days. This is a forecast, not a "
                 f"certainty."))
        elif outlook_severity(o) == "dry_season":
            lines.append(
                (f"Katika siku {o.horizon_days} zijazo hakuna mvua inayotarajiwa. "
                 f"Huu ni makadirio." if sw else
                 f"No rain is expected over the next {o.horizon_days} days. This is "
                 f"an estimate."))
        else:
            lines.append(
                (f"Katika siku {o.horizon_days} zijazo mvua inayotarajiwa ni "
                 f"milimita {o.forecast_total_mm:.0f} pekee. Huu ni makadirio." if sw
                 else f"Only {o.forecast_total_mm:.0f} mm of rain is expected over "
                      f"the next {o.horizon_days} days. This is an estimate."))
    lines.append(
        ("Ushauri: usisubiri mvua ianze ndipo usogeze mifugo. Kama malisho karibu na "
         "maji yanaisha, hamia taratibu sasa." if sw else
         "Advice: do not wait for the rains before you move the herd. If the "
         "pasture near water is running out, move gradually now."))
    return "\n".join(lines)
