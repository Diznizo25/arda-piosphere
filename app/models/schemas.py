from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Species = Literal["cattle", "shoat", "camel"]
Language = Literal["borana", "swahili"]


class AdvisoryRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    species: Species
    language: Language = "borana"


class AdvisoryResult(BaseModel):
    found: bool
    water_source_id: str | None = None
    distance_km: float | None = None
    source_type: str | None = None
    water_confidence: float | None = None
    last_confirmed: datetime | None = None
    forage_condition: str | None = None
    seasonally_normal: bool | None = None
    curing_stage_note: str | None = None
    water_reliability: str | None = None
    message: str
    raw_indices: dict[str, float] | None = None


class GroundTruthReportRequest(BaseModel):
    phone_number: str
    water_source_id: str | None = None
    report_type: Literal["water_dry", "water_available", "pasture_good", "pasture_poor", "other"]
    report_text: str | None = None


class CreateWaterSourceRequest(BaseModel):
    """Register a new water point from coordinates. Species rings are created
    automatically from config/species_rings.yaml."""
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    source_type: Literal["satellite_gsw", "osm", "wpdx", "ilri", "ground_truth"] = "ground_truth"
    source_ref: str | None = None
    ward: str | None = None
    county: str = "Isiolo"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class WaterSourceResponse(BaseModel):
    water_source_id: str
    lat: float
    lon: float
    source_type: str
    ward: str | None = None
    county: str
    status: str
    note: str
