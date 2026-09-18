"""
The shared forage classifier.

These tests encode the two things that went wrong before this module existed,
so they cannot come back:

  1. The map and the text used different precedence rules. Sweeping the
     plausible index domain they disagreed on 52.7% of what the text called
     "green" — both of the map's overwrite lines fired on green pixels.
  2. NaN fell through to a confident label. An all-NaN read became UNCERTAIN
     with seasonally_normal=False (because `nan >= 35` is False), which told a
     herder conditions were "worse than usual for this season" on the strength
     of bands that were never read.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.services.forage import (
    ForageClass,
    class_fractions,
    classify_array,
    classify_one,
    dominant_class,
    usable_fraction,
)


def name(ndvi, satvi, bsi) -> str:
    return classify_one(ndvi, satvi, bsi).name


# --- the three cases the product exists to get right -------------------------

def test_riparian_green_is_not_bare():
    """Dense green canopy along the river has low SWIR, so SATVI falls and BSI
    rises. It is the best grazing in the ward. The old map classifier painted
    it red while the text called it good."""
    assert name(0.52, 0.02, 0.31) == "GREEN_GROWING"


def test_cured_standing_grass_is_forage_not_bare():
    """The correction this whole system exists to make: cured grass reads
    low-NDVI, exactly like bare dirt, and only SATVI separates them."""
    assert name(0.18, 0.22, 0.14) == "DRY_FORAGE"


def test_genuinely_bare_ground():
    assert name(0.08, 0.02, 0.34) == "BARE_DEGRADED"


# --- greenness wins outright -------------------------------------------------

@pytest.mark.parametrize("satvi,bsi", [
    (0.02, 0.10),   # low SATVI alone used to overwrite green with bare
    (0.30, 0.31),   # high BSI alone used to overwrite green with bare
    (0.02, 0.31),   # both
])
def test_green_survives_both_former_overwrite_rules(satvi, bsi):
    assert name(0.50, satvi, bsi) == "GREEN_GROWING"


def test_no_later_rule_can_overwrite_an_earlier_one():
    """Regression guard for the sequential-assignment bug. Across the whole
    plausible domain, anything at/above the green threshold stays green."""
    from app.config import get_advisory_thresholds
    g = get_advisory_thresholds().vegetation["ndvi_green_threshold"]

    ndvi = np.arange(-0.1, 0.81, 0.05)
    satvi = np.arange(-0.1, 0.61, 0.05)
    bsi = np.arange(-0.3, 0.61, 0.05)
    N, S, B = np.meshgrid(ndvi, satvi, bsi, indexing="ij")
    cls = classify_array(N.ravel(), S.ravel(), B.ravel()).reshape(N.shape)

    assert (cls[N >= g] == ForageClass.GREEN_GROWING).all()


# --- NaN is a state, not a label ---------------------------------------------

def test_all_nan_is_nodata():
    assert name(np.nan, np.nan, np.nan) == "NODATA"


def test_partial_data_is_still_nodata():
    """A valid low SATVI with a cloud-masked NDVI used to produce a confident
    BARE_DEGRADED plus 'worse than usual for this season'."""
    assert name(np.nan, 0.02, 0.10) == "NODATA"
    assert name(0.50, np.nan, 0.10) == "NODATA"
    assert name(0.50, 0.30, np.nan) == "NODATA"


def test_infinities_are_nodata_too():
    assert name(np.inf, 0.2, 0.1) == "NODATA"


# --- fractions ---------------------------------------------------------------

def test_fractions_denominate_on_classifiable_pixels_only():
    """A ring that is 50% cloud must not report the cloud as bare ground."""
    cls = classify_array(
        np.array([0.50, 0.18, np.nan, np.nan]),
        np.array([0.30, 0.22, np.nan, np.nan]),
        np.array([0.10, 0.14, np.nan, np.nan]))
    fr = class_fractions(cls)
    assert fr[ForageClass.GREEN_GROWING] == pytest.approx(0.5)
    assert fr[ForageClass.DRY_FORAGE] == pytest.approx(0.5)
    assert sum(fr.values()) == pytest.approx(1.0)


def test_nothing_readable_returns_empty_not_zero():
    """Empty dict, not all-zeros: 'we could not see' must be distinguishable
    from 'we looked and there is nothing'."""
    nodata = np.zeros(5, dtype=np.uint8)
    assert class_fractions(nodata) == {}
    assert usable_fraction(nodata) is None
    assert dominant_class(nodata) is ForageClass.NODATA


def test_dominant_class_is_not_the_class_of_the_mean():
    """The bimodal ring: a green strip through bare ground. The MEAN of these
    pixels classifies as bare, a label describing no pixel present. The
    distribution keeps the strip visible."""
    green = np.full(17, 0.55), np.full(17, 0.28), np.full(17, 0.05)
    bare = np.full(83, 0.06), np.full(83, 0.03), np.full(83, 0.32)
    cls = classify_array(
        np.concatenate([green[0], bare[0]]),
        np.concatenate([green[1], bare[1]]),
        np.concatenate([green[2], bare[2]]))
    fr = class_fractions(cls)

    assert fr[ForageClass.GREEN_GROWING] == pytest.approx(0.17)
    assert fr[ForageClass.BARE_DEGRADED] == pytest.approx(0.83)
    assert usable_fraction(cls) == pytest.approx(0.17)
    # ...and the mean of the same pixels hides it entirely.
    assert classify_one(
        np.mean(np.concatenate([green[0], bare[0]])),
        np.mean(np.concatenate([green[1], bare[1]])),
        np.mean(np.concatenate([green[2], bare[2]])),
    ) is ForageClass.BARE_DEGRADED


# --- shape handling ----------------------------------------------------------

def test_scalars_and_arrays_both_work():
    assert classify_array(0.5, 0.3, 0.1).shape == (1,)
    assert classify_array(np.zeros((4, 5)), np.zeros((4, 5)), np.zeros((4, 5))).size == 20
