"""Aggregation queries + COG visualization for the live ops dashboard.

Every read here is fail-open: if a table is missing (migration not applied),
the database is flaky, or R2 is unreachable, we return partial data with a
`degraded` flag rather than 500-ing the dashboard. COG reads reuse the
memory-safe bounded read from raster_read (max 512px, all 8 bands).
"""
from __future__ import annotations

import io
import logging
import os
import time
from datetime import datetime, timezone

import numpy as np
from PIL import Image

from app.config import get_settings
from app.db import get_pg_connection
from app.services import raster_read
from app.services.storage import cog_key, get_s3_client

log = logging.getLogger(__name__)

# Small TTL caches so the dashboard does not hammer the DB/R2 on every poll.
_CACHE: dict[str, tuple[float, object]] = {}
_TTL_SECONDS = 30.0

_UPTIME_START = time.time()

# Band color ramps for the COG previews (RGBA stop lists, from low -> high).
_RAMP_RDYLGN = [(165, 0, 38), (215, 48, 39), (244, 165, 130), (253, 224, 139),
                (255, 255, 191), (166, 217, 106), (49, 163, 84), (0, 104, 55)]
_RAMP_VIRIDIS = [(68, 1, 84), (59, 82, 139), (33, 145, 140), (94, 201, 98),
                 (253, 231, 37)]


def _cache_get(key: str):
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL_SECONDS:
        return hit[1]
    return None


def _cache_put(key: str, value) -> None:
    if len(_CACHE) > 24:
        _CACHE.clear()
    _CACHE[key] = (time.time(), value)


def _table_exists(cur, table: str) -> bool:
    cur.execute(
        """select count(*) as n from information_schema.tables where table_name = %s""",
        (table,),
    )
    return cur.fetchone()["n"] == 1


def summary() -> dict:
    """Top-line KPIs across every table. Fail-open per block."""
    out: dict = {"degraded": False}

    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                def _count(sql, params=None):
                    cur.execute(sql, params or ())
                    return cur.fetchone()["n"]

                out["water_sources"] = {
                    "total": _count("select count(*) as n from water_sources"),
                    "by_type": _pairs(_rows(cur, """
                        select source_type as k, count(*)::int as v from water_sources
                        group by source_type order by v desc""")),
                    "by_ward": _pairs(_rows(cur, """
                        select coalesce(ward,'?') as k, count(*)::int as v
                        from water_sources group by ward order by v desc limit 12""")),
                    "with_zones": _count("""
                        select count(distinct ws.id) as n from water_sources ws
                        join piosphere_zones pz on pz.water_source_id = ws.id"""),
                    "zones_total": _count("select count(*) as n from piosphere_zones"),
                }
                out["pastoralists"] = {
                    "total": _count("select count(*) as n from pastoralists"),
                    "onboarded": _count(
                        "select count(*) as n from pastoralists where onboarded_at is not null"),
                    "by_language": _pairs(_rows(cur, """
                        select preferred_language as k, count(*)::int as v
                        from pastoralists group by preferred_language""")),
                    "by_species": _pairs(_rows(cur, """
                        select coalesce(primary_species,'unknown') as k, count(*)::int as v
                        from pastoralists group by primary_species""")),
                }



                if _table_exists(cur, "water_point_builds"):
                    out["builds"] = {
                        "by_status": _pairs(_rows(cur, """
                            select status as k, count(*)::int as v
                            from water_point_builds group by status""")),
                        "last_updated": _scalar(cur, """
                            select max(updated_at) from water_point_builds"""),
                    }
                if _table_exists(cur, "ground_truth_reports"):
                    out["ground_truth"] = {
                        "total": _count("select count(*) as n from ground_truth_reports"),
                        "last_14d": _count("""
                            select count(*) as n from ground_truth_reports
                            where reported_at > now() - interval '14 days'"""),
                        "by_type": _pairs(_rows(cur, """
                            select report_type as k, count(*)::int as v
                            from ground_truth_reports group by report_type order by v desc""")),
                    }
                if _table_exists(cur, "weight_records"):
                    out["weights"] = {
                        "records": _count("select count(*) as n from weight_records"),
                        "herd_estimates": _count(
                            "select count(*) as n from herd_estimates"),
                    }
                if _table_exists(cur, "conversation_state"):
                    out["active_flows"] = _count(
                        "select count(*) as n from conversation_state")
                if _table_exists(cur, "query_log"):
                    out["queries"] = {
                        "total": _count("select count(*) as n from query_log"),
                        "today": _count("""
                            select count(*) as n from query_log
                            where created_at > now() - interval '24 hours'"""),
                        "last_7d": _count("""
                            select count(*) as n from query_log
                            where created_at > now() - interval '7 days'"""),
                        "errors_7d": _count("""
                            select count(*) as n from query_log
                            where result = 'error' and created_at > now() - interval '7 days'"""),
                        "avg_latency_7d": _scalar(cur, """
                            select round(avg(latency_ms))::int from query_log
                            where latency_ms is not null
                              and created_at > now() - interval '7 days'"""),
                    }
    except Exception:  # noqa: BLE001
        log.exception("dashboard summary DB read failed")
        out["degraded"] = True
        out["db_error"] = True

    out["cogs"] = {"available": len(r2_available_ids())}
    out["uptime_seconds"] = int(time.time() - _UPTIME_START)
    out["generated_at"] = _now_iso()
    return out


def water_sources_geojson() -> dict:
    """All water points as GeoJSON, enriched with build status + COG presence."""
    feats: list[dict] = []
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    select ws.id, st_x(ws.geom) as lon, st_y(ws.geom) as lat,
                           ws.source_type, ws.source_ref, ws.name, ws.water_type,
                           ws.ward, ws.county,
                           ws.confidence, ws.last_confirmed, ws.created_at,
                           (select count(*) from piosphere_zones pz
                             where pz.water_source_id = ws.id) as zone_count,
                           b.status as build_status, b.progress as build_progress,
                           b.updated_at as build_updated
                    from water_sources ws
                    left join water_point_builds b on b.water_source_id = ws.id
                    order by ws.created_at desc
                """)
                rows = cur.fetchall()
    except Exception:  # noqa: BLE001
        log.exception("dashboard water-sources read failed")
        return {"type": "FeatureCollection", "features": [], "degraded": True}

    cogs = r2_available_ids()
    for r in rows:
        wid = str(r["id"])
        has_cog = wid in cogs
        build_status = r["build_status"] or ("built" if has_cog else "seed")
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(r["lon"]), float(r["lat"])]},
            "properties": {
                "id": wid,
                "name": r["name"],
                "water_type": r["water_type"],
                "source_type": r["source_type"],
                "ward": r["ward"],
                "county": r["county"],
                "confidence": float(r["confidence"] or 0),
                "zone_count": int(r["zone_count"] or 0),
                "has_cog": has_cog,
                "build_status": build_status,
                "build_progress": int(r["build_progress"] or 0),
                "created_at": _iso(r["created_at"]),
                "last_confirmed": _iso(r["last_confirmed"]),
            },
        })
    return {"type": "FeatureCollection", "features": feats}


def zones_geojson(water_source_id: str) -> dict:
    """The species rings for one water source (for the map layer)."""
    feats: list[dict] = []
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    select species, radius_km, st_asgeojson(geom) as geojson
                    from piosphere_zones where water_source_id = %s order by radius_km asc
                """, (water_source_id,))
                for r in cur.fetchall():
                    import json
                    feats.append({
                        "type": "Feature",
                        "properties": {"species": r["species"], "radius_km": float(r["radius_km"])},
                        "geometry": json.loads(r["geojson"]),
                    })
    except Exception:  # noqa: BLE001
        log.exception("zones read failed")
    return {"type": "FeatureCollection", "features": feats}



def activity(limit: int = 25) -> dict:
    """Combined recent activity: queries + build events + ground-truth reports."""
    events: list[dict] = []
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    select 'query' as ev_type, kind, phone, species, result,
                           latency_ms, water_source_id, created_at, detail
                    from query_log
                    order by created_at desc limit 25
                """)
                for r in cur.fetchall():
                    detail_text = ""
                    det = r["detail"]
                    if det:
                        try:
                            import json as _json
                            det = _json.loads(det) if isinstance(det, str) else det
                            if det.get("event") == "inbound":
                                detail_text = f"in {det.get('type', '')}: {det.get('text', '')}"[:80]
                            elif det.get("pipeline"):
                                detail_text = f"{det.get('scope', '')}"[:80]
                            elif det.get("error"):
                                detail_text = str(det.get("error"))[:80]
                        except Exception:  # noqa: BLE001
                            detail_text = str(det)[:80]
                    events.append({"type": "query", "kind": r["kind"], "phone": r["phone"],
                                   "species": r["species"], "result": r["result"],
                                   "latency_ms": r["latency_ms"],
                                   "water_source_id": str(r["water_source_id"]) if r["water_source_id"] else None,
                                   "detail_text": detail_text,
                                   "at": _iso(r["created_at"])})
                cur.execute("""
                    select 'build' as ev_type, status, stage, progress, error,
                           created_at, updated_at
                    from water_point_builds order by updated_at desc limit 10
                """)
                for r in cur.fetchall():
                    events.append({"type": "build", "status": r["status"], "stage": r["stage"],
                                   "progress": int(r["progress"] or 0), "error": r["error"],
                                   "at": _iso(r["updated_at"] or r["created_at"])})
                cur.execute("""
                    select 'report' as ev_type, report_type, report_text, reported_at
                    from ground_truth_reports order by reported_at desc limit 10
                """)
                for r in cur.fetchall():
                    events.append({"type": "report", "report_type": r["report_type"],
                                   "text": (r["report_text"] or "")[:120],
                                   "at": _iso(r["reported_at"])})
    except Exception:  # noqa: BLE001
        log.exception("activity read failed")
        return {"events": [], "degraded": True}

    events.sort(key=lambda e: e.get("at") or "", reverse=True)
    return {"events": events[:limit]}


def timeseries(days: int = 14) -> dict:
    """Daily query counts (by kind) and builds created, last N days."""
    out: dict = {"days": days, "queries": [], "builds": [], "labels": []}
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    select to_char(d, 'YYYY-MM-DD') as day
                    from generate_series(now() - interval '%s days', now(), interval '1 day') d
                """.replace("%s", str(days - 1)))
                labels = [r["day"] for r in cur.fetchall()]
                cur.execute("""
                    select to_char(date_trunc('day', created_at), 'YYYY-MM-DD') as day,
                           kind, count(*)::int as n
                    from query_log
                    where created_at > now() - interval '%s days'
                    group by 1, 2
                """.replace("%s", str(days)))
                qmap: dict[str, dict[str, int]] = {}
                for r in cur.fetchall():
                    qmap.setdefault(r["day"], {})[r["kind"]] = r["n"]
                cur.execute("""
                    select to_char(date_trunc('day', created_at), 'YYYY-MM-DD') as day,
                           count(*)::int as n
                    from water_point_builds
                    where created_at > now() - interval '%s days'
                    group by 1
                """.replace("%s", str(days)))
                bmap = {r["day"]: r["n"] for r in cur.fetchall()}

                kinds = sorted({k for d in qmap.values() for k in d})
                out["kinds"] = kinds
                for day in labels:
                    out["labels"].append(day)
                    out["queries"].append({k: qmap.get(day, {}).get(k, 0) for k in kinds})
                    out["builds"].append(bmap.get(day, 0))
    except Exception:  # noqa: BLE001
        log.exception("timeseries read failed")
        out["degraded"] = True
    return out



def cog_stats(water_source_id: str) -> dict:
    """Per-band stats (mean/min/max/std/coverage) from the memory-safe read."""
    res = raster_read.read_overview_array(water_source_id, max_dim=512)
    if res is None:
        return {"available": False}
    arr, _ = res
    names = raster_read.BAND_NAMES
    bands = []
    for i in range(min(len(names), arr.shape[0])):
        b = np.asarray(arr[i], dtype=np.float64)
        valid = b[np.isfinite(b)]
        if valid.size == 0:
            bands.append({"band": names[i], "valid_pixels": 0})
            continue
        bands.append({
            "band": names[i],
            "mean": round(float(valid.mean()), 4),
            "min": round(float(valid.min()), 4),
            "max": round(float(valid.max()), 4),
            "std": round(float(valid.std()), 4),
            "valid_pixels": int(valid.size),
            "total_pixels": int(b.size),
        })
    return {"available": True, "bands": bands, "water_source_id": water_source_id}


def _band_preview_key(water_source_id: str, band: int) -> str:
    return f"preview:{water_source_id}:{band}"


def cog_preview_png(water_source_id: str, band: int) -> bytes | None:
    """Small color-mapped PNG of one index band (192x192). Cached in-memory."""
    cached = _cache_get(_band_preview_key(water_source_id, band))
    if cached is not None:
        return cached
    res = raster_read.read_overview_array(water_source_id, max_dim=512)
    if res is None:
        return None
    arr, _ = res
    if band >= arr.shape[0]:
        return None
    data = np.asarray(arr[band], dtype=np.float64)
    valid = np.isfinite(data)
    if not valid.any():
        return None

    vmin = float(np.nanpercentile(data[valid], 2))
    vmax = float(np.nanpercentile(data[valid], 98))
    if vmax - vmin < 1e-9:
        vmin, vmax = vmin - 0.5, vmax + 0.5

    small = np.array(Image.fromarray(np.asarray(data, dtype=np.float32)).resize((192, 192)),
                     dtype=np.float64)
    norm = np.clip((small - vmin) / (vmax - vmin), 0.0, 1.0)
    nodata = ~np.isfinite(norm)   # NaN blended at edges / no-data cells
    norm = np.nan_to_num(norm, nan=0.0)

    ramp = np.array(_RAMP_RDYLGN if band in (0, 2, 6) else _RAMP_VIRIDIS, dtype=np.float64)
    idx = norm * (len(ramp) - 1)
    lo = np.floor(idx).astype(int)
    hi = np.minimum(lo + 1, len(ramp) - 1)
    frac = (idx - lo)[..., None]
    rgb = ramp[lo] * (1 - frac) + ramp[hi] * frac

    out = np.zeros((192, 192, 4), dtype=np.uint8)
    out[..., :3] = rgb.astype(np.uint8)
    out[..., 3] = (~nodata).astype(np.uint8) * 255

    img = Image.fromarray(out, "RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png = buf.getvalue()
    _cache_put(_band_preview_key(water_source_id, band), png)
    return png



def r2_available_ids() -> set[str]:
    """Set of water_source_ids that have an overview COG in R2 (cached 30s)."""
    cached = _cache_get("r2_cogs")
    if cached is not None:
        return cached
    ids: set[str] = set()
    try:
        settings = get_settings()
        client = get_s3_client()
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=settings.r2_bucket_name, Prefix="cogs/"):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("indices_ov8.tif"):
                    parts = key.split("/")
                    if len(parts) >= 2:
                        ids.add(parts[1])
    except Exception:  # noqa: BLE001
        log.exception("R2 listing failed")
    _cache_put("r2_cogs", ids)
    return ids


def system_health() -> dict:
    """DB + R2 reachability, uptime, last build/query timestamps, deploy commit."""
    health: dict = {"degraded": False, "checks": {}}
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("select 1 as ok")
                cur.fetchone()
                health["checks"]["db"] = "ok"
                if _table_exists(cur, "water_point_builds"):
                    cur.execute("select max(updated_at) as t from water_point_builds")
                    health["last_build_update"] = _iso(cur.fetchone()["t"])
                if _table_exists(cur, "query_log"):
                    cur.execute("select max(created_at) as t from query_log")
                    health["last_query"] = _iso(cur.fetchone()["t"])
    except Exception:  # noqa: BLE001
        health["checks"]["db"] = "error"
        health["degraded"] = True

    try:
        settings = get_settings()
        get_s3_client().head_bucket(Bucket=settings.r2_bucket_name)
        health["checks"]["r2"] = "ok"
    except Exception:  # noqa: BLE001
        health["checks"]["r2"] = "error"
        health["degraded"] = True

    health["cog_count"] = len(r2_available_ids())
    health["uptime_seconds"] = int(time.time() - _UPTIME_START)
    health["deploy_commit"] = os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("GIT_COMMIT") or "local"
    health["instance"] = os.environ.get("RENDER_INSTANCE_ID") or "local"
    health["environment"] = get_settings().environment
    health["generated_at"] = _now_iso()
    return health


# --- data freshness -----------------------------------------------------------
# "How old is the data behind an answer?" — the question a herder's trust depends
# on, and one that stays invisible unless we surface it. Each source is compared
# with the cadence it is SUPPOSED to have. The satellite snapshot comes from the R2
# object's upload time, because that is the file the advisory actually reads.

FRESHNESS_SOURCES = (
    # key, label, detail, expected seconds between updates
    ("satellite", "Satellite pasture (COG)",
     "Sentinel-2 index stack per water point", 14 * 86400),
    # NOTE: environment_daily.observed_on is a DATE, so it is structurally up to 24 h
    # old (today's rows are stored as 00:00). Expecting 12 h here would show a
    # permanent amber "aging" that is actually normal — a false alarm in a monitoring
    # panel costs more than it warns. A day is the right cadence for a daily series.
    ("rain_observed", "Rain observed", "Open-Meteo 90-day series (daily dates)", 86400),
    ("rain_forecast", "Rain forecast", "16-day outlook cache", 12 * 3600),
    ("climatology", "Rain climatology", "CHIRPS 30-year normals", 365 * 86400),
    ("herder_reports", "Herder reports", "water status + ground truth", 7 * 86400),
)


def freshness_status(age_seconds: int | None, expected_seconds: int) -> str:
    """missing | fresh | aging | stale — for one source (pure, testable)."""
    if age_seconds is None:
        return "missing"
    if age_seconds <= expected_seconds:
        return "fresh"
    if age_seconds <= expected_seconds * 2:
        return "aging"
    return "stale"


def _as_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def age_seconds(value, now: datetime | None = None) -> int | None:
    dt = _as_dt(value)
    if dt is None:
        return None
    return int(((now or datetime.now(timezone.utc)) - dt).total_seconds())


def _cog_age() -> dict:
    """Satellite snapshot age from R2 (what the reader actually opens).

    Cached for a few minutes: the dashboard polls, and HEAD-ing nine ~500 MB COGs on
    every poll is wasteful even though HEAD itself is cheap.
    """
    cached = _cache_get("cog_ages")
    if cached:
        return cached
    settings = get_settings()
    out = {"newest": None, "oldest": None, "built": 0, "total": 0, "missing": []}
    try:
        client = get_s3_client()
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("select id, name from water_sources order by created_at")
                points = cur.fetchall()
        out["total"] = len(points)
        for p in points:
            try:
                head = client.head_object(Bucket=settings.r2_bucket_name,
                                          Key=cog_key(str(p["id"])))
                lm = head["LastModified"]
                out["built"] += 1
                out["newest"] = lm if out["newest"] is None or lm > out["newest"] else out["newest"]
                out["oldest"] = lm if out["oldest"] is None or lm < out["oldest"] else out["oldest"]
            except Exception:  # noqa: BLE001
                out["missing"].append(p["name"] or str(p["id"])[:8])
    except Exception:  # noqa: BLE001
        log.exception("cog age check failed")
    _cache_put("cog_ages", out)
    return out

def data_freshness() -> dict:
    """Per-source freshness: what we hold, how old it is, and whether that is within
    the cadence we promised. This is the panel that answers "is the data current?"
    without anyone having to query the database by hand."""
    observed: dict[str, dict] = {}
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                observed["rain_observed"] = {
                    "newest": _scalar(cur, "select max(observed_on)::timestamp from environment_daily"),
                    "rows": _scalar(cur, "select count(*) from environment_daily"),
                }
                observed["rain_forecast"] = {
                    "newest": _scalar(cur, "select max(generated_at) from environment_forecast"),
                    "rows": _scalar(cur, "select count(*) from environment_forecast"),
                }
                observed["climatology"] = {
                    "newest": _scalar(cur, "select max(updated_at) from rainfall_climatology"),
                    "rows": _scalar(cur, "select count(*) from rainfall_climatology"),
                }
                if _table_exists(cur, "ground_truth_reports"):
                    observed["herder_reports"] = {
                        "newest": _scalar(cur, "select max(reported_at) from ground_truth_reports"),
                        "rows": _scalar(cur, "select count(*) from ground_truth_reports"),
                        "status_rows": _scalar(cur,
                                               "select count(status_updated_at) from water_sources"),
                    }
    except Exception:  # noqa: BLE001
        log.exception("freshness DB read failed")

    cog = _cog_age()
    sources: list[dict] = []
    for key, label, detail, expected in FRESHNESS_SOURCES:
        if key == "satellite":
            newest = cog["newest"]
            extra = (f"{cog['built']}/{cog['total']} water points built"
                     if cog["total"] else "no water points")
            if cog["missing"]:
                extra += f" · missing: {', '.join(cog['missing'][:2])}"
        else:
            info = observed.get(key) or {}
            newest = info.get("newest")
            extra = f"{info['rows']} rows" if info.get("rows") is not None else ""
            if key == "herder_reports" and info:
                extra += f" · status set on {info.get('status_rows', 0)} points"
        age = age_seconds(newest)
        sources.append({
            "key": key, "label": label, "detail": detail,
            "newest": _iso(newest), "age_seconds": age,
            "expected_seconds": expected,
            "status": freshness_status(age, expected),
            "extra": extra,
        })

    return {
        "generated_at": _now_iso(),
        "sources": sources,
        "satellite": {"built": cog["built"], "total": cog["total"],
                      "oldest": _iso(cog["oldest"]),
                      "oldest_age_seconds": age_seconds(cog["oldest"])},
        "pipelines": _pipeline_runs(),
    }


def _pipeline_runs() -> list[dict]:
    """When each scheduled pipeline last reported in (they log to query_log)."""
    out: list[dict] = []
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """select detail->>'pipeline' as pipeline,
                              max(created_at) as last_run,
                              count(*) as runs
                       from query_log
                       where detail ? 'pipeline'
                       group by 1 order by 1"""
                )
                for row in cur.fetchall():
                    out.append({"pipeline": row["pipeline"],
                                "last_run": _iso(row["last_run"]),
                                "age_seconds": age_seconds(row["last_run"]),
                                "runs": row["runs"]})
    except Exception:  # noqa: BLE001
        log.exception("pipeline run lookup failed")
    return out




# --- the questions a reader actually asks -------------------------------------
# "Is what we shipped live, and who is it reaching?" and "was the forecast right?"
# Neither was answerable from this page before. The menu a herder receives is
# decided by ARDA_FEATURES and by WhatsApp's 10-row cap, so a row can go missing
# without anything failing; and every projection used to be thrown away the moment
# its message went out, which made "18-25 days" impossible to score. Migration 016
# keeps the projection (depletion_forecasts) with the clues behind it. Both reads
# below stay fail-open, like the rest of this module.


def _menu_now() -> dict:
    """Exactly what a herder is offered right now, and which switch decided it.

    The router is imported lazily: it pulls in the whole chat stack, and a
    dashboard import error must never be the reason the bot stops answering.
    """
    try:
        from app.routers import whatsapp as wa
        from app.services import features
    except Exception:  # noqa: BLE001
        log.exception("menu introspection failed")
        return {"available": False}

    raw = (os.getenv(features.ENV_VAR) or "").strip()
    out: dict = {
        "available": True,
        "env_var": features.ENV_VAR,
        "from_env": bool(raw),
        "features": list(features.enabled_features()),
        "default": list(features.DEFAULT_FEATURES),
        "always_listed": sorted(features.ALWAYS_LISTED),
        "row_limit": features.MENU_ROW_LIMIT,
        "languages": {},
    }
    for lang in ("swahili", "english"):
        try:
            sections = wa._menu_sections(lang)
            rows = [
                {"id": rid, "label": label,
                 "service": wa.SERVICE_ALIASES.get(rid, rid.replace("svc:", ""))}
                for _title, section_rows in sections
                for rid, label, _desc in section_rows
            ]
            out["languages"][lang] = {"sections": [t for t, _ in sections],
                                      "rows": rows, "count": len(rows)}
        except Exception as exc:  # noqa: BLE001
            log.exception("menu introspection failed for %s", lang)
            out["languages"][lang] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def whatsapp_overview(limit: int = 20) -> dict:
    """Who is talking to us, what they said, and where their conversation sits.

    One row per registered herder (language, water point, flow state, last words,
    messages in 24h), plus the raw inbound feed. A table of states alone cannot
    show the herder who typed nothing but tapped the wrong row.
    """
    out: dict = {"generated_at": _now_iso(), "degraded": False, "menu": _menu_now(),
                 "herders": [], "messages": [], "totals": {}}
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select p.phone_number, p.full_name, p.preferred_language,
                           p.primary_species, p.water_interval, p.onboarded_at,
                           p.last_inbound_at, p.last_advisory_at,
                           ws.ward, coalesce(ws.name, ws.ward) as water_name,
                           cs.state, cs.updated_at as state_since,
                           (select count(*)::int from query_log q
                             where q.phone = p.phone_number
                               and q.created_at > now() - interval '24 hours') as msgs_24h,
                           (select left(coalesce(q.detail->>'text', ''), 160)
                              from query_log q
                             where q.phone = p.phone_number
                               and q.detail->>'event' = 'inbound'
                             order by q.created_at desc limit 1) as last_text
                      from pastoralists p
                      left join water_sources ws on ws.id = p.water_source_id
                      left join conversation_state cs on cs.phone_number = p.phone_number
                     order by coalesce(p.last_inbound_at, p.updated_at) desc nulls last
                     limit %s
                    """,
                    (limit,),
                )
                for r in cur.fetchall():
                    out["herders"].append({
                        "phone": r["phone_number"],
                        "name": r["full_name"],
                        "language": r["preferred_language"],
                        "species": r["primary_species"],
                        "interval": r["water_interval"],
                        "water_point": r["water_name"],
                        "ward": r["ward"],
                        "state": r["state"],
                        "state_since": _iso(r["state_since"]),
                        "onboarded_at": _iso(r["onboarded_at"]),
                        "last_inbound_at": _iso(r["last_inbound_at"]),
                        "last_advisory_at": _iso(r["last_advisory_at"]),
                        "last_text": r["last_text"],
                        "msgs_24h": r["msgs_24h"],
                    })

                cur.execute(
                    """
                    select phone, left(coalesce(detail->>'text', ''), 200) as text,
                           coalesce(detail->>'type', '') as kind, created_at
                      from query_log
                     where detail->>'event' = 'inbound'
                     order by created_at desc
                     limit %s
                    """,
                    (limit * 2,),
                )
                for r in cur.fetchall():
                    out["messages"].append({"phone": r["phone"], "text": r["text"],
                                            "kind": r["kind"], "at": _iso(r["created_at"])})

                cur.execute(
                    """
                    select (select count(*)::int from pastoralists) as herders,
                           (select count(*)::int from pastoralists
                             where last_inbound_at > now() - interval '24 hours')
                             as active_24h,
                           (select count(*)::int from query_log
                             where detail->>'event' = 'inbound'
                               and created_at > now() - interval '24 hours') as messages_24h,
                           (select count(*)::int from conversation_state) as flows,
                           (select count(*)::int from pastoralists
                             where onboarded_at is not null) as onboarded
                    """
                )
                out["totals"] = dict(cur.fetchone())
    except Exception:  # noqa: BLE001
        log.exception("whatsapp overview read failed")
        out["degraded"] = True
    return out


def forecast_board(limit: int = 25) -> dict:
    """The receipts: what we told him, why, and whether it came true.

    `receipts` is every forecast we issued (newest first) with the rate that
    produced it and the ring clue it leaned on. `scoreboard` counts the ones
    scripts/backtest_depletion.py has already judged. `rings` is the latest stored
    reading per ring, which is what the next forecast will be built from.
    """
    out: dict = {"generated_at": _now_iso(), "available": False, "degraded": False,
                 "detail": "", "receipts": [], "scoreboard": {}, "rings": []}
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                if not _table_exists(cur, "depletion_forecasts"):
                    out["detail"] = "migration 016 (depletion_forecasts) is not applied"
                    return out
                out["available"] = True

                cur.execute(
                    """
                    select df.id, df.issued_on, df.water_source_id, df.species,
                           df.interval, df.status, df.usable_pct,
                           df.slope_pp_week::float8 as slope_pp_week,
                           df.dry_slope_pp_week::float8 as dry_slope_pp_week,
                           df.rate_source, df.days_left, df.days_left_lo,
                           df.days_left_hi, df.samples, df.span_days, df.confidence,
                           df.cause, df.gap_pp, df.curing, df.snapshot_as_of,
                           df.outcome, df.runout_on, df.scored_on,
                           ws.ward, coalesce(ws.name, ws.ward) as water_name
                      from depletion_forecasts df
                      left join water_sources ws on ws.id = df.water_source_id
                     order by df.issued_on desc, df.id desc
                     limit %s
                    """,
                    (limit,),
                )
                for r in cur.fetchall():
                    out["receipts"].append({
                        "id": r["id"],
                        "issued_on": _iso(r["issued_on"]),
                        "water_source_id": str(r["water_source_id"]),
                        "water_point": r["water_name"],
                        "ward": r["ward"],
                        "species": r["species"],
                        "interval": r["interval"],
                        "status": r["status"],
                        "usable_pct": r["usable_pct"],
                        "slope_pp_week": r["slope_pp_week"],
                        "dry_slope_pp_week": r["dry_slope_pp_week"],
                        "rate_source": r["rate_source"],
                        "days_left": r["days_left"],
                        "days_left_lo": r["days_left_lo"],
                        "days_left_hi": r["days_left_hi"],
                        "samples": r["samples"],
                        "span_days": r["span_days"],
                        "confidence": r["confidence"],
                        "cause": r["cause"],
                        "gap_pp": r["gap_pp"],
                        "curing": r["curing"],
                        "snapshot_as_of": _iso(r["snapshot_as_of"]),
                        "outcome": r["outcome"],
                        "runout_on": _iso(r["runout_on"]),
                        "scored_on": _iso(r["scored_on"]),
                    })

                cur.execute(
                    """
                    select coalesce(outcome, 'unscored') as k, count(*)::int as v
                      from depletion_forecasts group by 1
                    """
                )
                outcomes = _pairs(cur.fetchall())
                cur.execute(
                    """
                    select coalesce(cause, 'unknown') as k, count(*)::int as v
                      from depletion_forecasts group by 1
                    """
                )
                causes = _pairs(cur.fetchall())
                decided = sum(outcomes.get(k, 0) for k in ("hit", "early", "late"))
                out["scoreboard"] = {
                    "outcomes": outcomes,
                    "causes": causes,
                    "total": sum(outcomes.values()),
                    "scored": sum(v for k, v in outcomes.items() if k != "unscored"),
                    # Of the forecasts a real runout followed, how many named it.
                    # no_collapse/unknown are excluded: a ring that never emptied
                    # says nothing about the accuracy of the date.
                    "hit_rate": (round(outcomes.get("hit", 0) / decided, 2)
                                 if decided else None),
                }

                cur.execute(
                    """
                    select distinct on (rps.water_source_id, rps.species, rps.interval)
                           rps.water_source_id, rps.species, rps.interval,
                           rps.sampled_on, rps.snapshot_as_of, rps.usable_pct,
                           rps.green_pct, rps.dry_pct, rps.bare_pct,
                           rps.ndmi::float8 as ndmi, rps.vci::float8 as vci,
                           rps.rain_14d_mm::float8 as rain_14d_mm,
                           rps.near_usable_pct, rps.far_usable_pct,
                           rps.patch_pct, rps.patch_count,
                           ws.ward, coalesce(ws.name, ws.ward) as water_name
                      from ring_pasture_samples rps
                      left join water_sources ws on ws.id = rps.water_source_id
                     order by rps.water_source_id, rps.species, rps.interval,
                              rps.sampled_on desc
                    """
                )
                for r in cur.fetchall():
                    near, far = r["near_usable_pct"], r["far_usable_pct"]
                    out["rings"].append({
                        "water_source_id": str(r["water_source_id"]),
                        "water_point": r["water_name"],
                        "ward": r["ward"],
                        "species": r["species"],
                        "interval": r["interval"],
                        "sampled_on": _iso(r["sampled_on"]),
                        "snapshot_as_of": _iso(r["snapshot_as_of"]),
                        "usable_pct": r["usable_pct"],
                        "green_pct": r["green_pct"],
                        "dry_pct": r["dry_pct"],
                        "bare_pct": r["bare_pct"],
                        "ndmi": r["ndmi"],
                        "vci": r["vci"],
                        "rain_14d_mm": r["rain_14d_mm"],
                        "near_pct": near,
                        "far_pct": far,
                        # positive = grass survives better away from the water,
                        # which is the spatial fingerprint of grazing pressure
                        "gap_pp": (None if near is None or far is None
                                   else int(far) - int(near)),
                        "patch_pct": r["patch_pct"],
                        "patch_count": r["patch_count"],
                    })
    except Exception:  # noqa: BLE001
        log.exception("forecast board read failed")
        out["degraded"] = True
    return out


# --- small helpers ------------------------------------------------------------


def _rows(cur, sql):
    cur.execute(sql)
    return cur.fetchall()


def _pairs(rows):
    """Convert [{k, v}, ...] dict-rows into {k: v}. (dict(rows) iterates the
    row KEYS, not the values — this helper does the right thing.)"""
    return {r["k"]: r["v"] for r in rows}


def _scalar(cur, sql):
    cur.execute(sql)
    row = cur.fetchone()
    if not row:
        return None
    vals = list(row.values())
    v = vals[0] if vals else None
    return _iso(v) if hasattr(v, "isoformat") else v


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if not hasattr(v, "isoformat"):
        return str(v)
    try:
        return v.isoformat(timespec="seconds")
    except TypeError:
        # datetime.date has no timespec argument
        return v.isoformat()
