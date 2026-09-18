"""
Environmental facts for the rain outlook: rainfall + soil moisture series and a
cached 16-day forecast per water point.

Architecture (same principle as the COGs): the request path NEVER calls a weather
API. scripts/refresh_environment.py (scheduled, or run by hand) writes
environment_daily + environment_forecast; this module reads those tables and hands
pure facts to app/services/forecast.py, which does the thinking.

Data source: Open-Meteo (ECMWF/GFS model output, CC-BY-4.0). Free and keyless on
the non-commercial tier (10k calls/day) - see README. A commercial deployment must
either license Open-Meteo's customer endpoint or self-host it; CHIRPS via GEE
remains the source of the LONG climatology either way.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import httpx
from psycopg.types.json import Jsonb

from app.db import get_pg_connection
from app.services import forecast as fc

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
SOURCE = "open-meteo"
PAST_DAYS = 90          # Open-Meteo's maximum for past_days on this endpoint
FORECAST_DAYS = 16      # beyond ~16 days there is no useful rain skill here
TIMEOUT_S = 25.0

UPSERT_DAILY_SQL = """
insert into environment_daily (water_source_id, observed_on, rain_mm, soil_moisture, source)
values (%(water_source_id)s, %(observed_on)s, %(rain_mm)s, %(soil_moisture)s, %(source)s)
on conflict (water_source_id, observed_on) do update
set rain_mm = excluded.rain_mm,
    soil_moisture = excluded.soil_moisture,
    source = excluded.source
"""

UPSERT_FORECAST_SQL = """
insert into environment_forecast
    (water_source_id, generated_at, horizon_days, total_mm, daily, source)
values (%(water_source_id)s, now(), %(horizon_days)s, %(total_mm)s, %(daily)s, %(source)s)
on conflict (water_source_id) do update
set generated_at = now(),
    horizon_days = excluded.horizon_days,
    total_mm = excluded.total_mm,
    daily = excluded.daily,
    source = excluded.source
"""

UPSERT_CLIMATOLOGY_SQL = """
insert into rainfall_climatology
    (water_source_id, month, mean_mm, p10_mm, p90_mm, years, source)
values (%(water_source_id)s, %(month)s, %(mean_mm)s, %(p10_mm)s, %(p90_mm)s,
        %(years)s, %(source)s)
on conflict (water_source_id, month) do update
set mean_mm = excluded.mean_mm,
    p10_mm = excluded.p10_mm,
    p90_mm = excluded.p90_mm,
    years = excluded.years,
    source = excluded.source,
    updated_at = now()
"""

RECENT_RAIN_SQL = """
select rain_mm, soil_moisture
from environment_daily
where water_source_id = %(water_source_id)s
order by observed_on desc
limit %(days)s
"""

CLIMATOLOGY_SQL = """
select month, mean_mm
from rainfall_climatology
where water_source_id = %(water_source_id)s
"""

FORECAST_SQL = """
select generated_at, horizon_days, total_mm, daily
from environment_forecast
where water_source_id = %(water_source_id)s
"""

POINTS_SQL = """
select id, st_x(geom) as lon, st_y(geom) as lat, name, ward
from water_sources
order by created_at asc
"""

# --- writes: fetch from the weather API and cache it (scripts only) ----------


def fetch_open_meteo(lat: float, lon: float, past_days: int = PAST_DAYS,
                     forecast_days: int = FORECAST_DAYS) -> dict | None:
    """Observed rain/soil moisture (past) + forecast (future) for one point.

    Returns observed (up to and including today) and forecast (tomorrow onward) as
    lists of {date, rain_mm, soil_moisture}. Returns None on any failure - a
    weather API outage must never break a refresh or a herder's reply.
    """
    try:
        resp = httpx.get(
            FORECAST_URL,
            params={
                "latitude": round(lat, 4),
                "longitude": round(lon, 4),
                "daily": "precipitation_sum,soil_moisture_0_to_7cm_mean",
                "past_days": past_days,
                "forecast_days": forecast_days,
                "timezone": "Africa/Nairobi",
            },
            timeout=TIMEOUT_S,
        )
        if resp.status_code != 200:
            log.warning("Open-Meteo returned %s: %.160s", resp.status_code, resp.text)
            return None
        daily = resp.json().get("daily") or {}
    except Exception:  # noqa: BLE001
        log.exception("Open-Meteo fetch failed (non-fatal)")
        return None

    dates = daily.get("time") or []
    rain = daily.get("precipitation_sum") or []
    soil = daily.get("soil_moisture_0_to_7cm_mean") or []
    today = date.today().isoformat()
    observed: list[dict] = []
    upcoming: list[dict] = []
    for i, day in enumerate(dates):
        row: dict = {
            "date": day,
            "rain_mm": float(rain[i]) if i < len(rain) and rain[i] is not None else 0.0,
        }
        if i < len(soil) and soil[i] is not None:
            row["soil_moisture"] = float(soil[i])
        (upcoming if day > today else observed).append(row)
    return {"observed": observed, "forecast": upcoming}


def refresh_for_water_source(water_source_id: str, lat: float, lon: float) -> dict:
    """Fetch and store the series + forecast for one water point.

    Returns a small report dict for the ops dashboard (rows written, horizon, total).
    """
    data = fetch_open_meteo(lat, lon)
    if not data:
        return {"water_source_id": water_source_id, "ok": False, "reason": "fetch_failed"}

    daily_rows = [
        {
            "water_source_id": water_source_id,
            "observed_on": r["date"],
            "rain_mm": r.get("rain_mm"),
            "soil_moisture": r.get("soil_moisture"),
            "source": SOURCE,
        }
        for r in data["observed"]
    ]
    fc_rows = [{"date": r["date"], "rain_mm": r.get("rain_mm") or 0.0}
               for r in data["forecast"]]
    total = round(sum(r["rain_mm"] for r in fc_rows), 2)

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            if daily_rows:
                cur.executemany(UPSERT_DAILY_SQL, daily_rows)
            # Jsonb() is explicit on purpose: in a multi-row VALUES list PostgreSQL
            # cannot infer the jsonb parameter type, so psycopg refuses a bare dict.
            cur.execute(UPSERT_FORECAST_SQL, {
                "water_source_id": water_source_id,
                "horizon_days": len(fc_rows),
                "total_mm": total,
                "daily": Jsonb(fc_rows),
                "source": SOURCE,
            })
        conn.commit()

    return {"water_source_id": water_source_id, "ok": True,
            "days_written": len(daily_rows), "horizon_days": len(fc_rows),
            "forecast_total_mm": total}


def list_points() -> list[dict]:
    """Every water point with its coordinates - the refresh scope."""
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(POINTS_SQL)
            rows = cur.fetchall()
    return [{"id": str(r["id"]), "lon": float(r["lon"]), "lat": float(r["lat"]),
             "name": r["name"], "ward": r["ward"]} for r in rows]


def recent_rain(water_source_id: str, days: int = 30) -> tuple[list[float], float | None]:
    """Rain (mm) for the last `days` days, oldest first, plus latest soil moisture.

    Oldest-first matters: forecast.dry_spell_days counts backwards from the end.
    """
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(RECENT_RAIN_SQL, {"water_source_id": water_source_id, "days": days})
            rows = cur.fetchall()
    rows = list(reversed(rows))
    rain = [float(r["rain_mm"] or 0.0) for r in rows]
    soil = next((float(r["soil_moisture"]) for r in reversed(rows)
                 if r["soil_moisture"] is not None), None)
    return rain, soil


def climatology(water_source_id: str) -> dict[int, float]:
    """month -> normal rainfall (mm) for this water point (CHIRPS 30-year)."""
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(CLIMATOLOGY_SQL, {"water_source_id": water_source_id})
            rows = cur.fetchall()
    return {int(r["month"]): float(r["mean_mm"]) for r in rows}


def store_climatology(water_source_id: str, monthly: dict[int, dict],
                      years: int, source: str = "chirps") -> int:
    """Persist per-month normals computed by scripts/build_rain_climatology.py.

    `monthly` maps month -> {"mean", "p10", "p90"}. Returns months written.
    """
    rows = [
        {
            "water_source_id": water_source_id,
            "month": int(month),
            "mean_mm": float(v["mean"]),
            "p10_mm": None if v.get("p10") is None else float(v["p10"]),
            "p90_mm": None if v.get("p90") is None else float(v["p90"]),
            "years": int(years),
            "source": source,
        }
        for month, v in sorted(monthly.items())
    ]
    if not rows:
        return 0
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(UPSERT_CLIMATOLOGY_SQL, rows)
        conn.commit()
    return len(rows)


def outlook(water_source_id: str, window_days: int = 30,
            as_of: date | None = None) -> fc.RainOutlook | None:
    """The stored rain outlook for a water point - DB reads only, no network.

    Returns None when nothing is stored yet (so callers can simply add no rain
    line). A missing forecast still yields an outlook from the observed series;
    only a missing series yields None.
    """
    as_of = as_of or datetime.now(timezone.utc).date()
    try:
        rain, soil = recent_rain(water_source_id, days=max(window_days, 90))
        clim = climatology(water_source_id)
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(FORECAST_SQL, {"water_source_id": water_source_id})
                frow = cur.fetchone()
    except Exception:  # noqa: BLE001
        log.exception("rain outlook read failed for %s (non-fatal)", water_source_id)
        return None

    if not rain and not frow:
        return None
    recent = rain[-window_days:] if rain else []
    daily: list[dict] = []
    generated_on: date | None = None
    if frow:
        daily = list(frow["daily"] or [])
        raw = frow["generated_at"]
        if isinstance(raw, datetime):
            generated_on = raw.date()
        elif raw:
            generated_on = date.fromisoformat(str(raw)[:10])
    return fc.build_outlook(
        recent_rain=recent,
        climatology=clim,
        daily_forecast=daily,
        window_days=window_days,
        as_of=as_of,
        forecast_generated_on=generated_on,
        soil_moisture=soil,
    )
