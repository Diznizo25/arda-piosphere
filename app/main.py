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

from app.config import get_settings
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
