"""Storage and wiring for the pasture-depletion forecast.

Three small jobs, kept out of the renderer and out of the router:

    record_sample()   one row per point/species/interval/day — history accumulates
                      from the weekly snapshot job (and from any advisory that
                      already paid for a blocking satellite read), never twice per
                      day. It keeps EVERYTHING the read produced: the class mix, the
                      index means (NDMI curing, VCI season), the near/far contrast,
                      the patch structure and how dry the fortnight was — the
                      predictors migration 016 added, which is what lets a forecast
                      be predicted rather than merely fitted.
    record_forecast() the answer we issued, in depletion_forecasts, so it can be
                      scored against what the ring actually did
                      (scripts/backtest_depletion.py). An unscored forecast is
                      folklore.
    forecast_for()    the Depletion for a point/species, with the rain forecast
                      attached as uncertainty (never as arithmetic).
    line_for()        the one-sentence form a WhatsApp reply can append, fail-open.

Why the sample is written where the status is read: the 8x overview read is the
expensive part (a few MB from R2 and a class computation). Recording the numbers
we already have costs one insert; recomputing them later would cost the read
again.

The forecast is only as good as the history, so `forecast_for` is allowed to say
"no_history" — see app/services/depletion.py for the rules and
scripts/test_depletion.py for the seasons it is pinned against.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from app.db import get_pg_connection
from app.services import depletion

log = logging.getLogger(__name__)

RECORD_SQL = """
insert into ring_pasture_samples
    (water_source_id, species, interval, sampled_on,
     usable_pct, green_pct, dry_pct, bare_pct, blank_pct,
     ndvi, satvi, bsi, ndmi, vci,
     near_usable_pct, far_usable_pct, patch_pct, patch_count,
     rain_14d_mm, soil_moisture, snapshot_as_of)
values
    (%(water_source_id)s, %(species)s, %(interval)s, %(sampled_on)s,
     %(usable_pct)s, %(green_pct)s, %(dry_pct)s, %(bare_pct)s, %(blank_pct)s,
     %(ndvi)s, %(satvi)s, %(bsi)s, %(ndmi)s, %(vci)s,
     %(near_usable_pct)s, %(far_usable_pct)s, %(patch_pct)s, %(patch_count)s,
     %(rain_14d_mm)s, %(soil_moisture)s,
     coalesce(%(snapshot_as_of)s::date,
              (select indices_as_of from water_sources where id = %(water_source_id)s)))
on conflict (water_source_id, species, interval, sampled_on) do update
set usable_pct      = excluded.usable_pct,
    green_pct       = excluded.green_pct,
    dry_pct         = excluded.dry_pct,
    bare_pct        = excluded.bare_pct,
    blank_pct       = excluded.blank_pct,
    -- The predictors themselves, so tomorrow's fit can use the curing signal and
    -- the near/far contrast instead of re-deriving them from a share.
    ndvi            = coalesce(excluded.ndvi, ring_pasture_samples.ndvi),
    satvi           = coalesce(excluded.satvi, ring_pasture_samples.satvi),
    bsi             = coalesce(excluded.bsi, ring_pasture_samples.bsi),
    ndmi            = coalesce(excluded.ndmi, ring_pasture_samples.ndmi),
    vci             = coalesce(excluded.vci, ring_pasture_samples.vci),
    near_usable_pct = coalesce(excluded.near_usable_pct,
                               ring_pasture_samples.near_usable_pct),
    far_usable_pct  = coalesce(excluded.far_usable_pct,
                               ring_pasture_samples.far_usable_pct),
    patch_pct       = coalesce(excluded.patch_pct, ring_pasture_samples.patch_pct),
    patch_count     = coalesce(excluded.patch_count, ring_pasture_samples.patch_count),
    rain_14d_mm     = coalesce(excluded.rain_14d_mm, ring_pasture_samples.rain_14d_mm),
    soil_moisture   = coalesce(excluded.soil_moisture,
                               ring_pasture_samples.soil_moisture),
    snapshot_as_of  = coalesce(excluded.snapshot_as_of,
                               ring_pasture_samples.snapshot_as_of),
    created_at = now()
"""

SAMPLES_SQL = """
select sampled_on, usable_pct, ndmi, vci, near_usable_pct, far_usable_pct,
       patch_pct, rain_14d_mm
from ring_pasture_samples
where water_source_id = %(water_source_id)s
  and species = %(species)s
  and interval = %(interval)s
  and sampled_on >= %(since)s
  and usable_pct is not null
order by sampled_on
"""

FORECAST_SQL = """
insert into depletion_forecasts
    (water_source_id, species, interval, issued_on, status, usable_pct,
     slope_pp_week, dry_slope_pp_week, rate_source, days_left, days_left_lo,
     days_left_hi, samples, span_days, confidence, cause, gap_pp, curing,
     snapshot_as_of)
values
    (%(water_source_id)s, %(species)s, %(interval)s, %(issued_on)s, %(status)s,
     %(usable_pct)s, %(slope_pp_week)s, %(dry_slope_pp_week)s, %(rate_source)s,
     %(days_left)s, %(days_left_lo)s, %(days_left_hi)s, %(samples)s,
     %(span_days)s, %(confidence)s, %(cause)s, %(gap_pp)s, %(curing)s,
     coalesce(%(snapshot_as_of)s::date,
              (select indices_as_of from water_sources where id = %(water_source_id)s)))
on conflict (water_source_id, species, interval, issued_on) do update
set status            = excluded.status,
    usable_pct        = excluded.usable_pct,
    slope_pp_week     = excluded.slope_pp_week,
    dry_slope_pp_week = excluded.dry_slope_pp_week,
    rate_source       = excluded.rate_source,
    days_left         = excluded.days_left,
    days_left_lo      = excluded.days_left_lo,
    days_left_hi      = excluded.days_left_hi,
    samples           = excluded.samples,
    span_days         = excluded.span_days,
    confidence        = excluded.confidence,
    cause             = excluded.cause,
    gap_pp            = excluded.gap_pp,
    curing            = excluded.curing,
    snapshot_as_of    = coalesce(excluded.snapshot_as_of,
                                 depletion_forecasts.snapshot_as_of)
"""

ALERT_SQL = """
insert into depletion_alerts (water_source_id, species, runout_week, recipients)
values (%(water_source_id)s, %(species)s, %(runout_week)s, 1)
on conflict (water_source_id, species, runout_week) do nothing
returning id
"""

ALERTED_SQL = """
select 1 from depletion_alerts
where water_source_id = %(water_source_id)s and species = %(species)s
  and runout_week = %(runout_week)s
"""


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _num(value) -> float | None:
    """A numeric that survived the row, or None - never a string in the DB."""
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _rain_context(water_source_id: str) -> tuple[float | None, float | None]:
    """(rain in the previous 14 days, latest soil moisture) from STORED data only.

    Never a live API call: this runs inside a WhatsApp reply's budget, and the
    question it answers - "was this reading taken in a dry spell?" - is about the
    last two weeks, which are already stored (migration 010).
    """
    try:
        from app.services import environment

        rain, soil = environment.recent_rain(water_source_id, days=14)
    except Exception:  # noqa: BLE001
        log.debug("rain context unavailable for %s (non-fatal)", water_source_id,
                  exc_info=True)
        return None, None
    if not rain:
        return None, _num(soil)
    return round(sum(rain), 2), _num(soil)


def record_sample(water_source_id: str, species: str, status: dict | None,
                  interval: str = "daily", on: date | None = None) -> bool:
    """Store one day's read. False when there was nothing measured to store.

    Everything the status offers beyond the class mix is kept: the index means,
    the near/far contrast, the patch structure and the rain context. Those are
    what make a projection possible later; the mix alone is one scalar.
    """
    if not water_source_id or not species or not status:
        return False
    usable = status.get("usable_pct")
    if usable is None:
        return False
    frac = status.get("frac") or {}
    idx = status.get("indices") or {}
    rain_14d, soil = _rain_context(water_source_id)
    row = {
        "water_source_id": water_source_id,
        "species": species,
        "interval": interval or "daily",
        "sampled_on": on or _today(),
        "usable_pct": int(round(float(usable))),
        "green_pct": frac.get("green"),
        "dry_pct": frac.get("dry"),
        "bare_pct": frac.get("bare"),
        "blank_pct": status.get("blank_pct"),
        # --- predictors (migration 016): stored as measured, or not at all ------
        "ndvi": _num(idx.get("ndvi")),
        "satvi": _num(idx.get("satvi")),
        "bsi": _num(idx.get("bsi")),
        "ndmi": _num(idx.get("ndmi")),
        "vci": _num(idx.get("vci")),
        "near_usable_pct": status.get("near_pct"),
        "far_usable_pct": status.get("far_pct"),
        "patch_pct": status.get("patch_pct"),
        "patch_count": status.get("patch_count"),
        "rain_14d_mm": rain_14d,
        "soil_moisture": soil,
        "snapshot_as_of": status.get("snapshot_as_of"),
    }
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(RECORD_SQL, row)
        return True
    except Exception:  # noqa: BLE001
        log.exception("pasture sample not stored for %s/%s (non-fatal)",
                      water_source_id, species)
        return False


def samples_for(water_source_id: str, species: str,
                days: int = 180, interval: str = "daily") -> list[depletion.Sample]:
    """The stored history for one ring, oldest first. Empty on any failure.

    `interval` filters the series on purpose: a daily ring and a every-2-days ring
    are different footprints (migration 016), and welding them into one trend line
    mixes a shrinking pasture with a growing one.
    """
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(SAMPLES_SQL, {
                    "water_source_id": water_source_id,
                    "species": species,
                    "interval": interval or "daily",
                    "since": _today() - timedelta(days=max(1, days)),
                })
                rows = cur.fetchall()
    except Exception:  # noqa: BLE001
        log.exception("pasture history read failed for %s/%s (non-fatal)",
                      water_source_id, species)
        return []
    out: list[depletion.Sample] = []
    for row in rows:
        def field(name: str, index: int):
            return row[name] if isinstance(row, dict) else row[index]

        day = field("sampled_on", 0)
        usable = field("usable_pct", 1)
        if isinstance(day, datetime):
            day = day.date()
        if day is None or usable is None:
            continue
        out.append(depletion.Sample(
            day=day,
            usable_pct=float(usable),
            ndmi=_num(field("ndmi", 2)),
            vci=_num(field("vci", 3)),
            near_pct=_num(field("near_usable_pct", 4)),
            far_pct=_num(field("far_usable_pct", 5)),
            patch_pct=_num(field("patch_pct", 6)),
            rain_14d_mm=_num(field("rain_14d_mm", 7)),
        ))
    return out


def record_forecast(water_source_id: str, species: str, dep: depletion.Depletion | None,
                    interval: str = "daily", on: date | None = None,
                    snapshot_as_of: str | None = None) -> bool:
    """Keep what we TOLD him, so the forecast can be scored later.

    Why this table exists at all: without it a projection of "18-25 days" becomes
    folklore. Nobody can tell whether the dry-period rate beat the long-run rate,
    whether the ±25% band was wide enough, or whether the curing signal fired
    before the collapse - because the numbers that were said out loud were never
    stored anywhere. scripts/backtest_depletion.py fills outcome/runout_on in.

    Same-day re-issues overwrite (the last answer of the day is the one he could
    have read), but outcome/scored_on/runout_on are NEVER touched here: they are
    the backtest's record of what actually happened.
    """
    if not water_source_id or not species or dep is None:
        return False
    row = {
        "water_source_id": water_source_id,
        "species": species,
        "interval": interval or "daily",
        "issued_on": on or _today(),
        "status": dep.status,
        "usable_pct": None if dep.usable_pct is None else int(round(dep.usable_pct)),
        "slope_pp_week": dep.slope_pp_week,
        "dry_slope_pp_week": dep.dry_slope_pp_week,
        "rate_source": dep.rate_source,
        "days_left": dep.days_left,
        "days_left_lo": dep.days_left_lo,
        "days_left_hi": dep.days_left_hi,
        "samples": dep.samples,
        "span_days": dep.span_days,
        "confidence": dep.confidence,
        "cause": dep.cause,
        "gap_pp": None if dep.gap_pp is None else int(round(dep.gap_pp)),
        "curing": bool(dep.curing),
        "snapshot_as_of": snapshot_as_of,
    }
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(FORECAST_SQL, row)
        return True
    except Exception:  # noqa: BLE001
        log.exception("forecast not stored for %s/%s (non-fatal)",
                      water_source_id, species)
        return False


def _rain_window(water_source_id: str, today: date) -> tuple[float | None, date | None]:
    """(forecast mm, onset date) from the stored outlook. Never a live API call."""
    try:
        from app.services import environment

        o = environment.outlook(water_source_id, window_days=30, as_of=today)
    except Exception:  # noqa: BLE001
        log.debug("rain outlook unavailable for depletion (non-fatal)", exc_info=True)
        return None, None
    if o is None or not o.has_forecast:
        return None, None
    return o.forecast_total_mm, o.onset_date


def forecast_for(water_source_id: str, species: str, interval: str = "daily",
                 today: date | None = None, record: bool = True,
                 history_days: int = 180) -> depletion.Depletion | None:
    """The depletion answer for one ring, or None when the ring has no read at all.

    Reads the satellite status (blocking, because the caller is about to quote a
    number), stores today's sample, then fits the stored history. Fully fail-open:
    a DB or raster problem costs the sentence, never the reply it was appended to.
    """
    if not water_source_id:
        return None
    try:
        from app.services import map_renderer

        status = map_renderer.pasture_overlay_status(
            water_source_id, species, interval or "daily", warm=False)
    except Exception:  # noqa: BLE001
        log.exception("pasture status read failed for %s (non-fatal)", water_source_id)
        return None
    if not status or status.get("usable_pct") is None:
        return None
    on = today or _today()
    if record:
        record_sample(water_source_id, species, status, interval=interval or "daily", on=on)
    history = samples_for(water_source_id, species, days=history_days,
                          interval=interval or "daily")
    rain_mm, rain_onset = _rain_window(water_source_id, on)
    dep = depletion.forecast(history, today=on, rain_mm=rain_mm, rain_onset=rain_onset)
    if record:
        # What was said out loud, kept: the only way this forecast can ever be
        # scored against what the ring actually did (scripts/backtest_depletion.py).
        record_forecast(water_source_id, species, dep, interval=interval or "daily",
                        on=on, snapshot_as_of=status.get("snapshot_as_of"))
    return dep


def line_for(water_source_id: str, species: str, interval: str = "daily",
             lang: str = "swahili") -> str:
    """The append-to-a-reply sentence ('' when there is nothing honest to say)."""
    try:
        dep = forecast_for(water_source_id, species, interval=interval)
        return depletion.line(dep, lang)
    except Exception:  # noqa: BLE001
        log.exception("depletion line failed (non-fatal)")
        return ""


def line_from_history(water_source_id: str, species: str, interval: str = "daily",
                      lang: str = "swahili", history_days: int = 180) -> str:
    """The sentence from STORED samples only — no satellite read.

    For places where waiting is unacceptable (a greeting, a list of "what changed"),
    the stored history and the stored rain outlook are enough: the newest sample is
    the reading, and the trend comes from the rest. Empty when nothing is stored.
    """
    try:
        history = samples_for(water_source_id, species, days=history_days,
                              interval=interval or "daily")
        if not history:
            return ""
        today = _today()
        rain_mm, rain_onset = _rain_window(water_source_id, today)
        return depletion.line(
            depletion.forecast(history, today=today, rain_mm=rain_mm, rain_onset=rain_onset),
            lang)
    except Exception:  # noqa: BLE001
        log.exception("stored-history depletion line failed (non-fatal)")
        return ""


def runout_week(dep: depletion.Depletion | None, today: date | None = None) -> date | None:
    """Monday of the week the projected run-out starts, or None.

    Used as the alert's idempotency key: warning about the SAME divergence twice
    is spam, warning about a divergence that moved two weeks closer is not.
    """
    if dep is None or dep.status not in ("projected", "already_low"):
        return None
    base = today or _today()
    start = base if dep.days_left_lo is None else base + timedelta(days=dep.days_left_lo)
    return start - timedelta(days=start.weekday())


def already_alerted(water_source_id: str, species: str, week: date) -> bool:
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(ALERTED_SQL, {"water_source_id": water_source_id,
                                          "species": species, "runout_week": week})
                return cur.fetchone() is not None
    except Exception:  # noqa: BLE001
        log.exception("alert dedupe read failed (treated as not sent)")
        return False


def record_alert(water_source_id: str, species: str, week: date) -> bool:
    """Claim this divergence. True when WE claimed it (i.e. it is not a duplicate)."""
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(ALERT_SQL, {"water_source_id": water_source_id,
                                        "species": species, "runout_week": week})
                return cur.fetchone() is not None
    except Exception:  # noqa: BLE001
        log.exception("alert dedupe write failed")
        return False


__all__ = [
    "record_sample", "samples_for", "record_forecast", "forecast_for", "line_for",
    "line_from_history", "runout_week", "already_alerted", "record_alert",
]
