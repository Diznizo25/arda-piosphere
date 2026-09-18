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

#: Minimum share of a ring that must be readable before we describe its forage.
#: Below this the honest answer is "we could not see", not a label inferred from
#: whatever was clear. Starting value — tune from the coverage distribution in
#: zone_index_history once a season of data exists (research note R0).
MIN_COVERAGE_RATIO = 0.25

LOW_COVERAGE_MESSAGE = {
    "swahili": ("Maji ya karibu yapo umbali wa {km:.1f} km.\n"
                "Mawingu yamezuia satelaiti wiki hii, kwa hivyo hatuwezi kusema "
                "hali ya malisho kwa uhakika. Tutakujulisha picha mpya ikipatikana."),
    "english": ("The nearest water is {km:.1f} km away.\n"
                "Cloud blocked the satellite this cycle, so we cannot say what the "
                "grazing is like with any confidence. We will have a clearer view soon."),
}


def _water_only_result(req: AdvisoryRequest, nearest, distance_km: float,
                       grazing_zone: str | None, effective_radius_km: float,
                       reason: str, coverage_ratio: float | None = None) -> AdvisoryResult:
    """Answer the water half honestly and decline to guess the forage half.

    Used when the satellite could not see enough of the ring. The water location
    is still real and still useful — only the pasture claim is withheld.

    The rain outlook is deliberately STILL included. It comes from the stored
    rainfall series, not from the clouded satellite imagery, so it is unaffected
    by the thing that suppressed the pasture claim — and it is arguably worth
    more here than usual, because it is the only forward-looking thing we can
    honestly say this cycle.
    """
    message = LOW_COVERAGE_MESSAGE[req.language].format(km=distance_km)
    outlook = _rain_outlook(nearest.water_source_id, req.language)
    if outlook and outlook[1]:
        message = f"{message}\n{outlook[1]}"
    o = outlook[0] if outlook else None

    return AdvisoryResult(
        found=True,
        water_source_id=nearest.water_source_id,
        distance_km=round(distance_km, 2),
        source_type=nearest.source_type,
        water_confidence=nearest.confidence,
        last_confirmed=nearest.last_confirmed,
        grazing_zone=grazing_zone,
        effective_radius_km=round(effective_radius_km, 1),
        coverage_ratio=round(coverage_ratio, 3) if coverage_ratio is not None else None,
        observed_at=getattr(nearest, "observed_at", None),
        no_forage_reason=reason,
        message=message,
        rain_dry_spell_days=(o.dry_spell_days if o else None),
        rain_deficit_pct=(o.deficit_pct if o else None),
        rain_forecast_mm=(o.forecast_total_mm if o else None),
        rain_onset_date=(o.onset_date.isoformat() if o and o.onset_date else None),
        rain_confidence=(o.confidence if o and o.has_forecast else None),
    )


def _rain_outlook(water_source_id: str, language: str):
    """(outlook, rendered_line) from the stored series + cached forecast.

    Purely a DB read — never a weather API call on the request path
    (architecture principle #1). Fail-open: no stored data means no rain line
    and an otherwise unchanged advisory.
    """
    try:
        from app.services import environment, forecast as fc

        outlook = environment.outlook(water_source_id, window_days=30)
        return outlook, fc.rain_line(outlook, language)
    except Exception:  # noqa: BLE001
        log.exception("rain outlook unavailable for %s (non-fatal)", water_source_id)
        return None


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

    # Cloud gate. Below this share of the ring readable, we genuinely do not
    # know what the forage is, and saying so is better than a confident label
    # inferred from whatever happened to be clear. Nothing could check this
    # before: coverage_ratio used to return pi/4 for any circle regardless of
    # cloud (see ZoneStats.coverage_ratio).
    if stats.coverage_ratio < MIN_COVERAGE_RATIO:
        log.info(
            "low coverage %.2f for water_source_id=%s species=%s — reporting water only",
            stats.coverage_ratio, nearest.water_source_id, req.species,
        )
        return _water_only_result(
            req, nearest, distance_km, grazing_zone, effective_radius_km,
            reason="low_coverage", coverage_ratio=stats.coverage_ratio,
        )

    assessment = classify_forage_condition(stats.means, stats.class_fractions)
    water_reliability = classify_water_reliability(stats.means.get("GSW_MONTHLY_RECURRENCE", 0.0))
    # Harsh/dry-season flag: degraded (overgrazed/bare) forage around this water
    # right now. We already classify it from the overview COG — no extra compute.
    dry_harsh = assessment.condition == ForageCondition.BARE_DEGRADED

    # Direction to the nearest walkable good patch. Already computed for the map
    # and previously discarded for the text — which is why a herder could be
    # told "bare ground, very little pasture" about a ring holding a green strip
    # they could have walked to.
    patch = None
    try:
        from app.services import map_renderer

        patch = map_renderer.pasture_guidance(
            nearest.water_source_id, req.lon, req.lat)
    except Exception:  # noqa: BLE001 — guidance is a bonus, never a failure
        log.debug("pasture_guidance unavailable", exc_info=True)

    message = format_advisory_message(
        language=req.language,
        species=req.species,
        distance_km=distance_km,
        condition=assessment.condition,
        seasonally_normal=assessment.seasonally_normal,
        curing_stage_note=assessment.curing_stage_note,
        water_reliability=water_reliability,
        grazing_zone=grazing_zone,
        effective_radius_km=effective_radius_km,
        dry_harsh=dry_harsh,
        class_fractions=assessment.class_fractions,
        patch_bearing_deg=patch[0] if patch else None,
        patch_distance_km=patch[1] if patch else None,
        observed_at=nearest.observed_at,
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
    res = _rain_outlook(nearest.water_source_id, req.language)
    outlook = res[0] if res else None
    if res and res[1]:
        message = f"{message}\n{res[1]}"

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
        forage_condition=assessment.condition.value,
        seasonally_normal=assessment.seasonally_normal,
        curing_stage_note=assessment.curing_stage_note,
        water_reliability=water_reliability.value,
        grazing_zone=grazing_zone,
        effective_radius_km=round(effective_radius_km, 1),
        message=message,
        raw_indices=assessment.raw,
        class_fractions=assessment.class_fractions,
        coverage_ratio=round(stats.coverage_ratio, 3),
        observed_at=nearest.observed_at,
        rain_dry_spell_days=(outlook.dry_spell_days if outlook else None),
        rain_deficit_pct=(outlook.deficit_pct if outlook else None),
        rain_forecast_mm=(outlook.forecast_total_mm if outlook else None),
        rain_onset_date=(outlook.onset_date.isoformat()
                         if outlook and outlook.onset_date else None),
        rain_confidence=(outlook.confidence if outlook and outlook.has_forecast else None),
    )
