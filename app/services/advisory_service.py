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
    # Herder-reported water status beats the satellite for "is there water TODAY":
    # if this point has never been confirmed, or was last confirmed long ago, say
    # so plainly instead of implying the water is fine.
    if nearest.needs_check:
        from app.services import water_status as ws

        label = ws.status_label(nearest.status, "swa" if req.language == "swahili" else "eng")
        age = ws.age_days(nearest.status_updated_at) or ws.age_days(nearest.last_confirmed)
        if req.language == "swahili":
            head = (f"⚠️ Maji haya: {label}"
                    + (f" (siku {round(age)} zilizopita)" if age is not None else ""))
            message = f"{head}\nHakikisha yapo kabla ya kuanza safari.\n\n{message}"
        else:
            head = (f"⚠️ This water: {label}"
                    + (f" ({round(age)} days ago)" if age is not None else ""))
            message = f"{head}\nConfirm before you set off.\n\n{message}"
    # Rain outlook from the stored series + cached forecast (migration 010). Purely
    # a DB read — never a weather API call on the request path. Fail-open: if we
    # have nothing stored, no rain line is added and the advisory is unchanged.
    outlook = None
    try:
        from app.services import environment, forecast as fc

        outlook = environment.outlook(nearest.water_source_id, window_days=30)
        line = fc.rain_line(outlook, req.language)
        if line:
            message = f"{message}\n{line}"
    except Exception:  # noqa: BLE001
        log.exception("rain outlook unavailable for %s (non-fatal)",
                      nearest.water_source_id)

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
        rain_dry_spell_days=(outlook.dry_spell_days if outlook else None),
        rain_deficit_pct=(outlook.deficit_pct if outlook else None),
        rain_forecast_mm=(outlook.forecast_total_mm if outlook else None),
        rain_onset_date=(outlook.onset_date.isoformat()
                         if outlook and outlook.onset_date else None),
        rain_confidence=(outlook.confidence if outlook and outlook.has_forecast else None),
    )
