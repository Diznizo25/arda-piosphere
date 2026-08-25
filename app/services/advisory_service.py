"""
Ties together water_reach -> raster_read -> advisory_logic -> i18n into the
single call the WhatsApp handler (and the plain HTTP endpoint) both use.
Nothing here calls GEE — see architecture principle #1.
"""
from __future__ import annotations

import logging

from app.models.schemas import AdvisoryRequest, AdvisoryResult
from app.services import water_reach, raster_read
from app.services.advisory_logic import classify_forage_condition, classify_water_reliability
from app.services.i18n import format_advisory_message

log = logging.getLogger(__name__)

NO_WATER_MESSAGE = {
    "swahili": "Samahani, hatuna maji yanayofikika kwa {species} karibu na eneo lako kwa sasa.",
    "borana": "Dhiifama, bishaan {species} keessaniif dhihoo bakka isin jirtan hin argamne.",
}

SPECIES_PLAIN = {
    ("swahili", "cattle"): "ng'ombe", ("swahili", "shoat"): "kondoo/mbuzi", ("swahili", "camel"): "ngamia",
    ("borana", "cattle"): "loon", ("borana", "shoat"): "hoolaa", ("borana", "camel"): "gaala",
}


def get_advisory(req: AdvisoryRequest) -> AdvisoryResult:
    candidates = water_reach.find_nearest_reachable_water(req.lon, req.lat, req.species, limit=1)

    if not candidates:
        species_label = SPECIES_PLAIN.get((req.language, req.species), req.species)
        return AdvisoryResult(
            found=False,
            message=NO_WATER_MESSAGE[req.language].format(species=species_label),
        )

    nearest = candidates[0]

    try:
        stats = raster_read.read_zone_stats(nearest.water_source_id, nearest.species_zone_geojson)
    except Exception as e:  # noqa: BLE001
        log.exception(f"Failed to read COG for water_source_id={nearest.water_source_id}")
        return AdvisoryResult(
            found=True,
            water_source_id=nearest.water_source_id,
            distance_km=nearest.distance_m / 1000,
            source_type=nearest.source_type,
            water_confidence=nearest.confidence,
            last_confirmed=nearest.last_confirmed,
            message=(
                f"Tunajua eneo la maji lakini data ya malisho haipatikani kwa sasa. "
                f"(COG_READ_ERROR: {type(e).__name__}: {e})"
                if req.language == "swahili"
                else (
                    f"Bakka bishaanii beekna, garuu odeeffannoon margaa yeroo ammaa hin argamne. "
                    f"(COG_READ_ERROR: {type(e).__name__}: {e})"
                )
            ),
        )

    forage = classify_forage_condition(stats.means)
    water_reliability = classify_water_reliability(stats.means.get("GSW_MONTHLY_RECURRENCE", 0.0))

    message = format_advisory_message(
        language=req.language,
        species=req.species,
        distance_km=nearest.distance_m / 1000,
        condition=forage.condition,
        seasonally_normal=forage.seasonally_normal,
        curing_stage_note=forage.curing_stage_note,
        water_reliability=water_reliability,
    )

    return AdvisoryResult(
        found=True,
        water_source_id=nearest.water_source_id,
        distance_km=round(nearest.distance_m / 1000, 2),
        source_type=nearest.source_type,
        water_confidence=nearest.confidence,
        last_confirmed=nearest.last_confirmed,
        forage_condition=forage.condition.value,
        seasonally_normal=forage.seasonally_normal,
        curing_stage_note=forage.curing_stage_note,
        water_reliability=water_reliability.value,
        message=message,
        raw_indices=forage.raw,
    )
