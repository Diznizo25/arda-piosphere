"""
Turns raw zonal-mean index values into the forage condition a herder actually
needs to hear. This is the domain-critical piece — read CLAUDE_CODE_PROMPT.md
"Satellite indices" section before changing anything here.

The rule this whole module exists to enforce: NEVER report "low vegetation"
or "poor pasture" from NDVI alone. In ASAL rangeland, most of the year's
useful forage is standing DRY grass, which reads as low-NDVI just like bare
ground does. SATVI (backed by BSI as a secondary cross-check) is what tells
the two apart. VCI then reframes "low" as normal-for-season or genuinely
abnormal, since dry-season lows are usually not a problem.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.config import get_advisory_thresholds


class ForageCondition(str, Enum):
    GREEN_GROWING = "green_growing"
    DRY_FORAGE_AVAILABLE = "dry_forage_available"
    BARE_DEGRADED = "bare_degraded"
    UNCERTAIN = "uncertain"


class WaterReliability(str, Enum):
    RELIABLE = "reliable"
    SEASONAL = "seasonal"
    UNRELIABLE = "unreliable"
    UNKNOWN = "unknown"


@dataclass
class ForageAssessment:
    condition: ForageCondition
    seasonally_normal: bool
    curing_stage_note: str | None
    raw: dict[str, float]


def classify_forage_condition(band_means: dict[str, float]) -> ForageAssessment:
    t = get_advisory_thresholds()
    veg = t.vegetation
    seasonal = t.seasonal

    ndvi = band_means.get("NDVI", float("nan"))
    satvi = band_means.get("SATVI", float("nan"))
    bsi = band_means.get("BSI", float("nan"))
    ndmi = band_means.get("NDMI", float("nan"))
    vci = band_means.get("VCI", float("nan"))

    if ndvi >= veg["ndvi_green_threshold"]:
        condition = ForageCondition.GREEN_GROWING
    elif satvi >= veg["satvi_dry_forage_threshold"] and bsi <= veg["bsi_low_threshold"]:
        # The core correction this system exists to make: high SATVI + low BSI
        # alongside low NDVI is standing dry forage, not bare/poor land.
        condition = ForageCondition.DRY_FORAGE_AVAILABLE
    elif satvi < veg["satvi_bare_threshold"] or bsi >= veg["bsi_high_threshold"]:
        condition = ForageCondition.BARE_DEGRADED
    else:
        condition = ForageCondition.UNCERTAIN

    # VCI: is "low" actually abnormal for this time of year, or just normal dry season?
    seasonally_normal = True
    if condition in (ForageCondition.BARE_DEGRADED, ForageCondition.UNCERTAIN):
        seasonally_normal = vci >= seasonal["vci_abnormally_poor_threshold"]

    curing_note = None
    if condition == ForageCondition.DRY_FORAGE_AVAILABLE:
        curing_note = "still_curing" if ndmi > veg["ndmi_curing_threshold"] else "fully_cured"

    return ForageAssessment(
        condition=condition,
        seasonally_normal=seasonally_normal,
        curing_stage_note=curing_note,
        raw={"NDVI": ndvi, "SATVI": satvi, "BSI": bsi, "NDMI": ndmi, "VCI": vci},
    )


def classify_water_reliability(gsw_monthly_recurrence: float | None) -> WaterReliability:
    """JRC surface-water monthly recurrence -> reliability, with an explicit
    UNKNOWN state when the COG has no valid GSW data for the zone (NaN/None).
    Never report "unreliable" just because the data is missing — that would tell
    a herder a water point is bad when we simply don't know."""
    import math

    t = get_advisory_thresholds()
    if gsw_monthly_recurrence is None or math.isnan(gsw_monthly_recurrence):
        return WaterReliability.UNKNOWN
    if gsw_monthly_recurrence >= t.water["gsw_reliable_threshold"]:
        return WaterReliability.RELIABLE
    if gsw_monthly_recurrence >= t.water["gsw_seasonal_threshold"]:
        return WaterReliability.SEASONAL
    return WaterReliability.UNRELIABLE
