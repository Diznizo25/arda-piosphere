"""
Ties together water_reach -> raster_read -> advisory_logic -> i18n into the
single call the WhatsApp handler (and the plain HTTP endpoint) both use.
Nothing here calls GEE — see architecture principle #1.
"""
from __future__ import annotations

import logging

from app.config import get_species_rings
from app.models.schemas import AdvisoryRequest, AdvisoryResult
from app.services import ai, water_reach, raster_read
from app.services.advisory_logic import (
    ForageCondition,
    classify_forage_condition,
    classify_water_reliability,
)
from app.services.i18n import format_advisory_message

log = logging.getLogger(__name__)

NO_WATER_MESSAGE = {
    "swahili": "Samahani, hatuna maji yanayofikika kwa {species} karibu na eneo lako kwa sasa.",
    "english": "Sorry, there is no reachable water for {species} near your location right now.",
}

SPECIES_PLAIN = {
    ("swahili", "cattle"): "ng'ombe", ("swahili", "shoat"): "kondoo/mbuzi", ("swahili", "camel"): "ngamia",
    ("english", "cattle"): "cattle", ("english", "shoat"): "sheep/goats", ("english", "camel"): "camels",
}


def get_advisory(req: AdvisoryRequest) -> AdvisoryResult:
    """Public entry: computes the advisory and logs the query for the dashboard.

    Logging is fail-open (a DB hiccup never breaks the advisory reply)."""
    from app.services import query_log

    t = query_log.timer()
    try:
        res = _get_advisory_impl(req)
    except Exception:
        query_log.log_query(kind="advisory", lat=req.lat, lon=req.lon, species=req.species,
                            result="error", latency_ms=t.ms())
        raise
    query_log.log_query(
        kind="advisory",
        lat=req.lat,
        lon=req.lon,
        species=req.species,
        water_source_id=res.water_source_id,
        result="ok" if res.found else "not_found",
        latency_ms=t.ms(),
        detail={"distance_km": res.distance_km} if res.distance_km else None,
    )
    return res


def _get_advisory_impl(req: AdvisoryRequest) -> AdvisoryResult:
    rings = get_species_rings()
    # Effective reach = species max ring scaled by the herder's watering interval
    # (capped at the satellite compute ring). No recompute — a read-time scale.
    effective_radius_km = rings.effective_radius_km(req.species, req.water_interval)
    candidates = water_reach.find_nearest_reachable_water(
        req.lon, req.lat, req.species, limit=1,
        effective_radius_km=effective_radius_km)

    if not candidates:
        species_label = SPECIES_PLAIN.get((req.language, req.species), req.species)
        return AdvisoryResult(
            found=False,
            message=NO_WATER_MESSAGE[req.language].format(species=species_label),
        )

    nearest = candidates[0]
    distance_km = nearest.distance_m / 1000
    grazing_zone = rings.grazing_zone(distance_km, req.species, req.water_interval)

    try:
        stats = raster_read.read_zone_stats(nearest.water_source_id, nearest.species_zone_geojson)
    except Exception as e:  # noqa: BLE001
        log.exception(f"Failed to read COG for water_source_id={nearest.water_source_id}")
        return AdvisoryResult(
            found=True,
            water_source_id=nearest.water_source_id,
            distance_km=round(distance_km, 2),
            source_type=nearest.source_type,
            water_confidence=nearest.confidence,
            last_confirmed=nearest.last_confirmed,
            grazing_zone=grazing_zone,
            effective_radius_km=round(effective_radius_km, 1),
            message=(
                f"Tunajua eneo la maji lakini data ya malisho haipatikani kwa sasa. "
                f"(COG_READ_ERROR: {type(e).__name__}: {e})"
                if req.language == "swahili"
                else (
                    f"We know the water location but pasture data is not available right now. "
                    f"(COG_READ_ERROR: {type(e).__name__}: {e})"
                )
            ),
        )

    forage = classify_forage_condition(stats.means)
    water_reliability = classify_water_reliability(stats.means.get("GSW_MONTHLY_RECURRENCE", 0.0))
    # Harsh/dry-season flag: degraded (overgrazed/bare) forage around this water
    # right now. We already classify it from the overview COG — no extra compute.
    dry_harsh = forage.condition == ForageCondition.BARE_DEGRADED

    message = format_advisory_message(
        language=req.language,
        species=req.species,
        distance_km=distance_km,
        condition=forage.condition,
        seasonally_normal=forage.seasonally_normal,
        curing_stage_note=forage.curing_stage_note,
        water_reliability=water_reliability,
        grazing_zone=grazing_zone,
        effective_radius_km=effective_radius_km,
        dry_harsh=dry_harsh,
    )
    # The LLM may only rephrase the deterministic text, never add facts; on any
    # failure the original message is returned (see app/services/ai.py).
    message = ai.rephrase_advisory(
        language=req.language,
        base_message=message,
        distance_km=distance_km,
    )

    return AdvisoryResult(
        found=True,
        water_source_id=nearest.water_source_id,
        distance_km=round(distance_km, 2),
        source_type=nearest.source_type,
        water_confidence=nearest.confidence,
        last_confirmed=nearest.last_confirmed,
        forage_condition=forage.condition.value,
        seasonally_normal=forage.seasonally_normal,
        curing_stage_note=forage.curing_stage_note,
        water_reliability=water_reliability.value,
        grazing_zone=grazing_zone,
        effective_radius_km=round(effective_radius_km, 1),
        message=message,
        raw_indices=forage.raw,
    )
