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
insert into environment_daily (water_source_id, observed_on, rain_mm, soil_moisture,
                               temperature_max_c, temperature_min_c, humidity, source)
values (%(water_source_id)s, %(observed_on)s, %(rain_mm)s, %(soil_moisture)s,
        %(temperature_max_c)s, %(temperature_min_c)s, %(humidity)s, %(source)s)
on conflict (water_source_id, observed_on) do update
set rain_mm = excluded.rain_mm,
    soil_moisture = excluded.soil_moisture,
    temperature_max_c = excluded.temperature_max_c,
    temperature_min_c = excluded.temperature_min_c,
    humidity = excluded.humidity,
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

# Full daily rows (oldest first at the caller), for the pest/parasite windows:
# they need warmth and humidity alongside the rain.
RECENT_SERIES_SQL = """
select observed_on, rain_mm, soil_moisture, temperature_max_c, temperature_min_c,
       humidity
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
    lists of {date, rain_mm, soil_moisture, temperature_max_c, temperature_min_c,
    humidity}. Returns None on any failure - a weather API outage must never break
    a refresh or a herder's reply.

    Temperature and humidity are here for the pest/parasite windows: tick questing
    and worm larval development need warmth as well as moisture, and flies need
    humid air. They are read from the same daily block, so the pest layer costs no
    extra API calls.
    """
    try:
        resp = httpx.get(
            FORECAST_URL,
            params={
                "latitude": round(lat, 4),
                "longitude": round(lon, 4),
                "daily": ("precipitation_sum,soil_moisture_0_to_7cm_mean,"
                          "temperature_2m_max,temperature_2m_min,"
                          "relative_humidity_2m_mean"),
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
    tmax = daily.get("temperature_2m_max") or []
    tmin = daily.get("temperature_2m_min") or []
    humid = daily.get("relative_humidity_2m_mean") or []

    def _at(series, i):
        return float(series[i]) if i < len(series) and series[i] is not None else None

    today = date.today().isoformat()
    observed: list[dict] = []
    upcoming: list[dict] = []
    for i, day in enumerate(dates):
        row: dict = {
            "date": day,
            "rain_mm": float(rain[i]) if i < len(rain) and rain[i] is not None else 0.0,
        }
        if (v := _at(soil, i)) is not None:
            row["soil_moisture"] = v
        if (v := _at(tmax, i)) is not None:
            row["temperature_max_c"] = v
        if (v := _at(tmin, i)) is not None:
            row["temperature_min_c"] = v
        if (v := _at(humid, i)) is not None:
            row["humidity"] = v
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
            "temperature_max_c": r.get("temperature_max_c"),
            "temperature_min_c": r.get("temperature_min_c"),
            "humidity": r.get("humidity"),
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


def recent_series(water_source_id: str, days: int = 90) -> list[dict]:
    """Daily rows oldest-first, with rain, soil moisture, temperature, humidity.

    Used by the pest/parasite windows. Nothing is invented when a column is empty
    (older rows predate the temperature columns): the caller sees None and the
    window simply has fewer signals, which is the honest outcome.
    """
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(RECENT_SERIES_SQL, {"water_source_id": water_source_id,
                                            "days": days})
            rows = cur.fetchall()
    return [dict(r) for r in reversed(rows)]


# Last 7 days of rain at the nearest OTHER points, with the direction from here:
# the raw material for the "where did it rain" sentence. The comparison is done in
# words (see forecast.place_rain_line) rather than drawn on a map, because our rain
# data is a point value, not a picture of the ground.
NEARBY_RAIN_SQL = """
select ws.id, ws.name, ws.ward,
       st_distance(ws.geom::geography, me.geom::geography) as distance_m,
       st_x(ws.geom) as lon,
       st_y(ws.geom) as lat,
       coalesce(sum(ed.rain_mm) filter (
           where ed.observed_on >= current_date - interval '7 days'), 0) as rain_7d_mm
from water_sources me
join water_sources ws on ws.id <> me.id
left join environment_daily ed on ed.water_source_id = ws.id
where me.id = %(id)s
group by ws.id, ws.name, ws.ward, ws.geom, me.geom
order by distance_m asc
limit %(limit)s
"""


def own_rain_7d(water_source_id: str, days: int = 7) -> float | None:
    """Rain at the herder's own point over the last `days` (None when no data)."""
    rows = recent_series(water_source_id, days=days)
    if not rows:
        return None
    return round(sum(float(r.get("rain_mm") or 0.0) for r in rows), 1)


def nearby_rain(water_source_id: str, limit: int = 3) -> list[dict]:
    """Rain in the last 7 days at the nearest other points, with direction.

    Returns [{name, ward, rain_7d_mm, distance_km, direction_swa}]. Points without
    a name are dropped by the caller's formatter: "a point with no name got 18 mm"
    tells a herder nothing.
    """
    from app.services.map_renderer import _bearing_deg, _compass_swa

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("select st_y(geom) as lat, st_x(geom) as lon "
                        "from water_sources where id = %(id)s",
                        {"id": water_source_id})
            here = cur.fetchone()
        if not here:
            return []
        with conn.cursor() as cur:
            cur.execute(NEARBY_RAIN_SQL, {"id": water_source_id, "limit": limit})
            rows = cur.fetchall()
    out = []
    for r in rows:
        bearing = _bearing_deg(float(here["lat"]), float(here["lon"]),
                               float(r["lat"]), float(r["lon"]))
        out.append({
            "name": r["name"] or r["ward"],
            "ward": r["ward"],
            "rain_7d_mm": round(float(r["rain_7d_mm"] or 0.0), 1),
            "distance_km": round(float(r["distance_m"]) / 1000.0, 1),
            "direction_swa": _compass_swa(bearing),
        })
    return out


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
