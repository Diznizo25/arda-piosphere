from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Species = Literal["cattle", "shoat", "camel"]
Language = Literal["swahili", "english"]


class AdvisoryRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    species: Species
    language: Language = "swahili"
    water_interval: Literal["daily", "every_2_3_days"] = "daily"


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
    #: What the satellite SAW at the point (water_seen / no_water_seen /
    #: uncertain / unknown). Separate from water_reliability, which is a
    #: multi-decade climatology and says nothing about today.
    water_presence: str | None = None
    grazing_zone: str | None = None
    effective_radius_km: float | None = None
    message: str
    raw_indices: dict[str, float] | None = None
    # Rain outlook (migration 010). Always an ESTIMATE: dry_spell_days is observed,
    # forecast_* is model output, and confidence says which horizon it is good for.
    rain_dry_spell_days: int | None = None
    rain_deficit_pct: float | None = None
    rain_forecast_mm: float | None = None
    rain_onset_date: str | None = None
    rain_confidence: str | None = None
    #: Share of classifiable ring pixels per forage class. The headline number a
    #: herder can act on — a ring mean of the same pixels can read "bare" while
    #: a fifth of the ring is grazeable.
    class_fractions: dict[str, float] | None = None
    #: Share of the ring the satellite could actually read this cycle.
    coverage_ratio: float | None = None
    #: Imagery date behind the forage claim, so a caller can judge its age.
    observed_at: datetime | None = None
    #: Set when the forage half was deliberately withheld ("low_coverage",
    #: "stale_data"). Distinguishes "we looked and it is bare" from "we could
    #: not look" — which the old API could not express.
    no_forage_reason: str | None = None


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
    # Optional build-tracker info: who pinned it (E.164) + preferred language,
    # so the scheduled builder can notify them with progress.
    created_by: str | None = None
    language: Language = "swahili"


class WaterSourceResponse(BaseModel):
    water_source_id: str
    lat: float
    lon: float
    source_type: str
    ward: str | None = None
    county: str
    status: str
    note: str
