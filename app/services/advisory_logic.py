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

import math
from dataclasses import dataclass
from enum import Enum

from app.config import get_advisory_thresholds
from app.services import forage


class ForageCondition(str, Enum):
    GREEN_GROWING = "green_growing"
    DRY_FORAGE_AVAILABLE = "dry_forage_available"
    BARE_DEGRADED = "bare_degraded"
    UNCERTAIN = "uncertain"


class WaterReliability(str, Enum):
    """How often this spot USUALLY holds water in this calendar month.

    Derived from JRC Global Surface Water monthly recurrence — a multi-decade
    climatology. Historical, never present-tense. See WaterPresence below.
    """
    RELIABLE = "reliable"
    SEASONAL = "seasonal"
    UNRELIABLE = "unreliable"
    UNKNOWN = "unknown"


class WaterPresence(str, Enum):
    """Whether open water was actually SEEN here when the satellite last looked.

    A different question from WaterReliability, and reported separately. A pan
    can be bone dry for three months and still be "reliable" for September,
    because the climatology says September is usually wet — which is how a
    herder ended up being told `maji ya kutegemewa kipindi hiki` about water
    that was not there.

    UNCERTAIN is a first-class state. NDWI between the two thresholds, or a
    window too small to trust, means we cannot tell — which must never be
    reported as "no water", since that would send a herd elsewhere on a guess.
    """
    WATER_SEEN = "water_seen"
    NO_WATER_SEEN = "no_water_seen"
    UNCERTAIN = "uncertain"
    UNKNOWN = "unknown"


#: Below this many valid pixels the window is too small to draw any conclusion.
MIN_WATER_SAMPLE_PX = 4


@dataclass
class ForageAssessment:
    condition: ForageCondition
    #: True = normal for the season, False = abnormally poor, None = NOT KNOWN.
    #: None matters: it is the difference between "this is worse than usual" and
    #: "we could not tell", and only one of those should reach a herder.
    seasonally_normal: bool | None
    curing_stage_note: str | None
    raw: dict[str, float]
    #: Share of classifiable ring pixels per forage class, when the caller has
    #: it. A ring that is 17% green riverine strip and 83% bare averages to
    #: "bare" — a label describing no pixel in it — so the fractions, not the
    #: mean, are what should be reported.
    class_fractions: dict[str, float] | None = None
    #: True when no band could be read at all.
    no_data: bool = False


#: Map the shared classifier's classes onto the advisory's public vocabulary.
_CLASS_TO_CONDITION = {
    forage.ForageClass.GREEN_GROWING: ForageCondition.GREEN_GROWING,
    forage.ForageClass.DRY_FORAGE: ForageCondition.DRY_FORAGE_AVAILABLE,
    forage.ForageClass.BARE_DEGRADED: ForageCondition.BARE_DEGRADED,
    forage.ForageClass.UNCERTAIN: ForageCondition.UNCERTAIN,
}


def classify_forage_condition(
    band_means: dict[str, float],
    class_fractions: dict[forage.ForageClass, float] | None = None,
) -> ForageAssessment:
    """Classify a ring from its band means, and from its class distribution
    when the caller has one.

    `class_fractions` is strongly preferred. The means are kept because the
    curing-stage and seasonal-normality signals (NDMI, VCI) are genuinely
    ring-level questions, but the headline condition should come from what the
    pixels actually are, not from what their average looks like.
    """
    t = get_advisory_thresholds()
    veg = t.vegetation
    seasonal = t.seasonal

    ndvi = band_means.get("NDVI", float("nan"))
    satvi = band_means.get("SATVI", float("nan"))
    bsi = band_means.get("BSI", float("nan"))
    ndmi = band_means.get("NDMI", float("nan"))
    vci = band_means.get("VCI", float("nan"))
    raw = {"NDVI": ndvi, "SATVI": satvi, "BSI": bsi, "NDMI": ndmi, "VCI": vci}

    if class_fractions:
        dominant = max(class_fractions, key=lambda c: class_fractions[c])
        condition = _CLASS_TO_CONDITION[dominant]
        readable = True
        fractions = {c.name.lower(): v for c, v in class_fractions.items()}
    else:
        cls = forage.classify_one(ndvi, satvi, bsi)
        readable = cls is not forage.ForageClass.NODATA
        condition = _CLASS_TO_CONDITION.get(cls, ForageCondition.UNCERTAIN)
        fractions = None

    # Nothing readable: say so, and do NOT fall through to a confident label.
    # The previous version reached UNCERTAIN here and then set
    # seasonally_normal=False, because `nan >= 35` is False — which told a
    # herder conditions were "worse than usual for this season" on the strength
    # of bands that were never read.
    if not readable:
        return ForageAssessment(
            condition=ForageCondition.UNCERTAIN,
            seasonally_normal=None,
            curing_stage_note=None,
            raw=raw,
            class_fractions=None,
            no_data=True,
        )

    # VCI: is "low" actually abnormal for this time of year, or just normal dry
    # season? Unknown when VCI itself could not be read.
    seasonally_normal: bool | None = True
    if condition in (ForageCondition.BARE_DEGRADED, ForageCondition.UNCERTAIN):
        seasonally_normal = (
            None if math.isnan(vci)
            else vci >= seasonal["vci_abnormally_poor_threshold"]
        )

    curing_note = None
    if condition == ForageCondition.DRY_FORAGE_AVAILABLE and not math.isnan(ndmi):
        curing_note = "still_curing" if ndmi > veg["ndmi_curing_threshold"] else "fully_cured"

    return ForageAssessment(
        condition=condition,
        seasonally_normal=seasonally_normal,
        curing_stage_note=curing_note,
        raw=raw,
        class_fractions=fractions,
    )


def classify_water_presence(ndwi: float | None,
                            sample_px: int = 0) -> WaterPresence:
    """Present-tense open water at the point, from NDWI (McFeeters).

    Deliberately conservative in both directions. Asymmetry matters here:
    wrongly saying "no water" sends a herd on a longer trek than necessary,
    while wrongly saying "water" sends them to a dry hole. So anything between
    the two thresholds is UNCERTAIN, not a guess either way, and the herder's
    own report always outranks this (see water_status.py).
    """
    if ndwi is None or math.isnan(ndwi):
        return WaterPresence.UNKNOWN
    if sample_px < MIN_WATER_SAMPLE_PX:
        return WaterPresence.UNKNOWN

    t = get_advisory_thresholds().water
    if ndwi >= t["ndwi_open_water_threshold"]:
        return WaterPresence.WATER_SEEN
    if ndwi < t["ndwi_no_water_threshold"]:
        return WaterPresence.NO_WATER_SEEN
    return WaterPresence.UNCERTAIN


def classify_water_reliability(gsw_monthly_recurrence: float | None) -> WaterReliability:
    """JRC surface-water monthly recurrence -> reliability, with an explicit
    UNKNOWN state when the COG has no valid GSW data for the zone (NaN/None).
    Never report "unreliable" just because the data is missing — that would tell
    a herder a water point is bad when we simply don't know."""
    t = get_advisory_thresholds()
    if gsw_monthly_recurrence is None or math.isnan(gsw_monthly_recurrence):
        return WaterReliability.UNKNOWN
    if gsw_monthly_recurrence >= t.water["gsw_reliable_threshold"]:
        return WaterReliability.RELIABLE
    if gsw_monthly_recurrence >= t.water["gsw_seasonal_threshold"]:
        return WaterReliability.SEASONAL
    return WaterReliability.UNRELIABLE
