"""
Arda Link — Piosphere Grazing Advisory (Phase 1)

FastAPI reads only precomputed results (Postgres/PostGIS vector data + COGs
in object storage). It never calls Google Earth Engine live — that only
happens in scripts/gee_compute_export.py, run on a schedule outside the
request path.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db import get_pg_connection
from app.routers import advisory, ground_truth, run, whatsapp
from app.services.storage import get_s3_client
from app.services.gee_auth import init_earth_engine

settings = get_settings()
logging.basicConfig(level=settings.log_level)

app = FastAPI(
    title="Arda Link — Piosphere Grazing Advisory",
    description="Phase 1: water reach + dry-forage-aware grazing advisory over WhatsApp.",
    version="0.1.0",
)

app.include_router(advisory.router)
app.include_router(whatsapp.router)
app.include_router(ground_truth.router)
app.include_router(run.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "environment": settings.environment}


@app.get("/health/db")
def health_db():
    """Diagnostic only — confirms DATABASE_URL is wired and the Phase 1
    schema/migration actually landed. Real per-table counts, not a mock."""
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("select count(*) as n from water_sources")
                water_sources = cur.fetchone()["n"]
                cur.execute("select count(*) as n from piosphere_zones")
                piosphere_zones = cur.fetchone()["n"]
                cur.execute("select count(*) as n from pastoralists")
                pastoralists = cur.fetchone()["n"]
        return {
            "status": "ok",
            "water_sources": water_sources,
            "piosphere_zones": piosphere_zones,
            "pastoralists": pastoralists,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})


@app.get("/health/gee")
def health_gee():
    """Diagnostic only — confirms the Earth Engine service account actually
    authenticates and can run a trivial server-side computation."""
    try:
        init_earth_engine()
        import ee
        result = ee.Number(1).add(1).getInfo()
        return {"status": "ok", "test_computation_1_plus_1": result}
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})


@app.get("/health/r2")
def health_r2():
    """Diagnostic only — confirms R2 credentials/bucket are wired by doing a
    real head_bucket call against Cloudflare R2."""
    settings = get_settings()
    try:
        client = get_s3_client()
        client.head_bucket(Bucket=settings.r2_bucket_name)
        return {"status": "ok", "bucket": settings.r2_bucket_name}
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})
