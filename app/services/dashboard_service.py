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

import numpy as np
from PIL import Image

from app.config import get_settings
from app.db import get_pg_connection
from app.services import raster_read
from app.services.storage import get_s3_client

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
                           ws.source_type, ws.source_ref, ws.name, ws.ward, ws.county,
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
    return v.isoformat(timespec="seconds") if hasattr(v, "isoformat") else str(v)

    return out
