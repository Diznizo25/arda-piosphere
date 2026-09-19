"""
Single source of truth for turning index values into a forage class.

Both callers use this — the text advisory (one ring, scalar means) and the map
renderer (per pixel, vectorised) — so a herder can never be told one thing in
words and shown another in colour.

Before this module existed there were two classifiers with different precedence
rules. Sweeping the plausible index domain, they disagreed on 52.7% of what the
text called "green growing pasture": the map's sequential-overwrite form applied
`satvi < bare` and `bsi >= bare` AFTER assigning green, so both could repaint a
green pixel red. The realistic instance is riparian vegetation — a dense green
canopy along the Ewaso has low SWIR reflectance, so SATVI falls and BSI rises.
That strip is the best grazing in the ward. The words called it good; the map
painted it bare.

Precedence, highest first. This is the ORDER, not a set of independent tests:

  green      NDVI at/above the green threshold. Wins outright — see above.
  dry        SATVI at/above the dry-forage threshold AND BSI below the bare
             cutoff. The correction this whole system exists to make: standing
             cured grass reads low-NDVI, exactly like bare dirt, and only SATVI
             separates them.
  bare       SATVI below the bare threshold OR BSI at/above the bare cutoff.
  uncertain  anything left — reported as unknown, never guessed.

Pixels with any non-finite input are NODATA: never coloured, never counted in a
percentage, never averaged into an answer. The old scalar path had no such state
— an all-NaN read fell through to UNCERTAIN and then set seasonally_normal=False
(because `nan >= 35` is False), which told a herder conditions were "worse than
usual for this season" on the strength of bands that were never read.

Thresholds live in config/advisory_thresholds.yaml. Never hardcode them here.
"""
from __future__ import annotations

from enum import IntEnum

import numpy as np

from app.config import get_advisory_thresholds


class ForageClass(IntEnum):
    NODATA = 0
    GREEN_GROWING = 1
    DRY_FORAGE = 2
    BARE_DEGRADED = 3
    UNCERTAIN = 4


#: Classes an animal can actually eat.
USABLE = (ForageClass.GREEN_GROWING, ForageClass.DRY_FORAGE)

#: Band positions in the COG stack (see raster_read.BAND_NAMES). The three bands
#: this classifier needs, as 0-based indexes into that stack.
I_NDVI, I_SATVI, I_BSI = 0, 2, 3


def classify_array(ndvi, satvi, bsi) -> np.ndarray:
    """Vectorised forage classification -> uint8 array of ForageClass values.

    Accepts scalars or arrays of any matching shape. The returned array is
    always at least 1-D, so callers can treat scalar and array inputs alike.
    """
    t = get_advisory_thresholds().vegetation
    ndvi = np.atleast_1d(np.asarray(ndvi, dtype="float64"))
    satvi = np.atleast_1d(np.asarray(satvi, dtype="float64"))
    bsi = np.atleast_1d(np.asarray(bsi, dtype="float64"))

    valid = np.isfinite(ndvi) & np.isfinite(satvi) & np.isfinite(bsi)

    green = ndvi >= t["ndvi_green_threshold"]
    dry = (satvi >= t["satvi_dry_forage_threshold"]) & (bsi < t["bsi_high_threshold"])
    bare = (satvi < t["satvi_bare_threshold"]) | (bsi >= t["bsi_high_threshold"])

    # np.select is first-match-wins, which is exactly the if/elif chain it
    # replaces — and, unlike sequential assignment, a later condition cannot
    # silently overwrite an earlier one.
    return np.select(
        [~valid, green, dry, bare],
        [ForageClass.NODATA, ForageClass.GREEN_GROWING,
         ForageClass.DRY_FORAGE, ForageClass.BARE_DEGRADED],
        default=ForageClass.UNCERTAIN,
    ).astype(np.uint8)


def classify_one(ndvi: float, satvi: float, bsi: float) -> ForageClass:
    """Scalar convenience wrapper for the advisory path."""
    return ForageClass(int(classify_array(ndvi, satvi, bsi).reshape(-1)[0]))


def class_fractions(classes: np.ndarray) -> dict[ForageClass, float]:
    """Share of CLASSIFIABLE pixels in each class. Empty dict when unreadable.

    The denominator is classifiable pixels, never total pixels: a ring that is
    90% cloud must not report "10% usable pasture" as though the other 90% were
    bare ground. Callers distinguish "mostly bare" from "mostly unseen" by
    reading coverage separately.
    """
    classified = int((classes != ForageClass.NODATA).sum())
    if not classified:
        return {}
    return {
        c: float((classes == c).sum()) / classified
        for c in (ForageClass.GREEN_GROWING, ForageClass.DRY_FORAGE,
                  ForageClass.BARE_DEGRADED, ForageClass.UNCERTAIN)
    }


def usable_fraction(classes: np.ndarray) -> float | None:
    """Share of classifiable pixels an animal can graze, or None if unreadable."""
    fr = class_fractions(classes)
    if not fr:
        return None
    return fr[ForageClass.GREEN_GROWING] + fr[ForageClass.DRY_FORAGE]


def dominant_class(classes: np.ndarray) -> ForageClass:
    """The most common classifiable class, or NODATA when nothing is readable.

    Used for the headline label. Note this is deliberately NOT the class of the
    ring MEAN: the mean of a bimodal ring (a green strip through bare ground)
    lands in a class that describes no pixel in it.
    """
    fr = class_fractions(classes)
    if not fr:
        return ForageClass.NODATA
    return max(fr, key=lambda c: fr[c])
