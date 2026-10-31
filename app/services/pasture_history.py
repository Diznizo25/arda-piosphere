"""Storage and wiring for the pasture-depletion forecast.

Three small jobs, kept out of the renderer and out of the router:

    record_sample()   one row per point/species/day — history accumulates from the
                      weekly snapshot job (and from any advisory that already paid
                      for a blocking satellite read), never twice per day.
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
     usable_pct, green_pct, dry_pct, bare_pct, blank_pct)
values
    (%(water_source_id)s, %(species)s, %(interval)s, %(sampled_on)s,
     %(usable_pct)s, %(green_pct)s, %(dry_pct)s, %(bare_pct)s, %(blank_pct)s)
on conflict (water_source_id, species, sampled_on) do update
set interval   = excluded.interval,
    usable_pct = excluded.usable_pct,
    green_pct  = excluded.green_pct,
    dry_pct    = excluded.dry_pct,
    bare_pct   = excluded.bare_pct,
    blank_pct  = excluded.blank_pct,
    created_at = now()
"""

SAMPLES_SQL = """
select sampled_on, usable_pct
from ring_pasture_samples
where water_source_id = %(water_source_id)s
  and species = %(species)s
  and sampled_on >= %(since)s
  and usable_pct is not null
order by sampled_on
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


def record_sample(water_source_id: str, species: str, status: dict | None,
                  interval: str = "daily", on: date | None = None) -> bool:
    """Store one day's read. False when there was nothing measured to store."""
    if not water_source_id or not species or not status:
        return False
    usable = status.get("usable_pct")
    if usable is None:
        return False
    frac = status.get("frac") or {}
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
                days: int = 180) -> list[depletion.Sample]:
    """The stored history for one ring, oldest first. Empty on any failure."""
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(SAMPLES_SQL, {
                    "water_source_id": water_source_id,
                    "species": species,
                    "since": _today() - timedelta(days=max(1, days)),
                })
                rows = cur.fetchall()
    except Exception:  # noqa: BLE001
        log.exception("pasture history read failed for %s/%s (non-fatal)",
                      water_source_id, species)
        return []
    out: list[depletion.Sample] = []
    for row in rows:
        day = row["sampled_on"] if isinstance(row, dict) else row[0]
        usable = row["usable_pct"] if isinstance(row, dict) else row[1]
        if isinstance(day, datetime):
            day = day.date()
        if day is None or usable is None:
            continue
        out.append(depletion.Sample(day=day, usable_pct=float(usable)))
    return out


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
    history = samples_for(water_source_id, species, days=history_days)
    rain_mm, rain_onset = _rain_window(water_source_id, on)
    return depletion.forecast(history, today=on, rain_mm=rain_mm, rain_onset=rain_onset)


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
        history = samples_for(water_source_id, species, days=history_days)
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
    "record_sample", "samples_for", "forecast_for", "line_for", "line_from_history",
    "runout_week", "already_alerted", "record_alert",
]
