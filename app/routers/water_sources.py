"""Water-source registry API.

Lets an operator (or the WhatsApp pin flow) register a brand-new water point
from just coordinates. The point's three species rings are buffered in PostGIS
immediately; GEE compute for it is triggered separately (scripts/gee_compute_export.py
--water-source <id>, or scripts/pin_water_point.py --compute) because batch
compute runs outside the web instance.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.schemas import CreateWaterSourceRequest, WaterSourceResponse
from app.services import water_sources

router = APIRouter(prefix="/water-sources", tags=["water-sources"])


@router.post("", response_model=WaterSourceResponse, status_code=201)
def create_water_source(req: CreateWaterSourceRequest) -> WaterSourceResponse:
    """Register a water point from coordinates + create its species rings."""
    try:
        ws = water_sources.create_water_source(
            lon=req.lon,
            lat=req.lat,
            source_type=req.source_type,
            source_ref=req.source_ref,
            ward=req.ward,
            county=req.county,
            confidence=req.confidence,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to register water source: {e}")
    return WaterSourceResponse(
        water_source_id=ws.id,
        lat=ws.lat,
        lon=ws.lon,
        source_type=ws.source_type,
        ward=ws.ward,
        county=ws.county,
        status="registered",
        note=(
            "Water point and species rings created. Grazing data will appear after "
            "the GEE compute+transfer for this point completes."
        ),
    )


@router.get("")
def list_water_sources() -> dict:
    return {"water_sources": [vars(ws) for ws in water_sources.list_water_sources()]}
