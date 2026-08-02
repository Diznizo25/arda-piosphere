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
from app.routers import advisory, ground_truth, whatsapp

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
