"""
Present-tense water (NDWI) versus historical reliability (JRC GSW).

These are two different questions and the whole point of Phase 2 is that they
stay separate. `classify_water_reliability` answers "is this usually wet in
September"; `classify_water_presence` answers "did the satellite see water here
when it last looked". Conflating them is how a herder gets told a pan that has
been dry for three months is `maji ya kutegemewa kipindi hiki`.
"""
from __future__ import annotations

import math

import pytest

from app.services.advisory_logic import (
    MIN_WATER_SAMPLE_PX,
    WaterPresence,
    WaterReliability,
    classify_water_presence,
    classify_water_reliability,
)

ENOUGH = MIN_WATER_SAMPLE_PX + 1


def test_open_water_is_seen():
    assert classify_water_presence(0.45, ENOUGH) is WaterPresence.WATER_SEEN


def test_dry_ground_reads_as_no_water():
    """Bare soil and vegetation both give strongly negative NDWI."""
    assert classify_water_presence(-0.35, ENOUGH) is WaterPresence.NO_WATER_SEEN


def test_the_middle_band_is_uncertain_not_a_guess():
    """Asymmetric cost: saying 'no water' wrongly sends a herd on a longer trek,
    saying 'water' wrongly sends them to a dry hole. Between the thresholds we
    say neither."""
    assert classify_water_presence(0.10, ENOUGH) is WaterPresence.UNCERTAIN


def test_missing_or_nan_is_unknown_not_no_water():
    assert classify_water_presence(None, ENOUGH) is WaterPresence.UNKNOWN
    assert classify_water_presence(float("nan"), ENOUGH) is WaterPresence.UNKNOWN


def test_a_window_too_small_to_trust_is_unknown():
    """A couple of clear pixels at 80 m is not evidence about a borehole."""
    assert classify_water_presence(0.9, MIN_WATER_SAMPLE_PX - 1) is WaterPresence.UNKNOWN
    assert classify_water_presence(0.9, 0) is WaterPresence.UNKNOWN


@pytest.mark.parametrize("ndwi,expected", [
    (0.20, WaterPresence.WATER_SEEN),      # at the threshold, inclusive
    (0.19, WaterPresence.UNCERTAIN),
    (0.00, WaterPresence.UNCERTAIN),       # at the no-water threshold, exclusive
    (-0.01, WaterPresence.NO_WATER_SEEN),
])
def test_threshold_boundaries(ndwi, expected):
    assert classify_water_presence(ndwi, ENOUGH) is expected


# --- the two signals are independent ----------------------------------------

def test_a_reliable_point_can_be_observed_dry():
    """The case that motivated this whole phase. JRC says September is usually
    wet here; the satellite says there is nothing in the pan today. Both are
    true, and the herder needs to hear the second one."""
    reliability = classify_water_reliability(85.0)      # historically reliable
    presence = classify_water_presence(-0.30, ENOUGH)   # nothing there now

    assert reliability is WaterReliability.RELIABLE
    assert presence is WaterPresence.NO_WATER_SEEN


def test_reliability_still_handles_missing_data_as_unknown():
    """Unchanged behaviour — guarding against a regression while editing."""
    assert classify_water_reliability(None) is WaterReliability.UNKNOWN
    assert classify_water_reliability(float("nan")) is WaterReliability.UNKNOWN


# --- wording ----------------------------------------------------------------

def test_only_definite_states_produce_a_line():
    """UNCERTAIN and UNKNOWN must stay silent rather than hedge out loud."""
    from datetime import datetime, timezone

    from app.services.i18n import _presence_line

    when = datetime(2026, 9, 14, tzinfo=timezone.utc)
    assert _presence_line(WaterPresence.WATER_SEEN, when, False) is not None
    assert _presence_line(WaterPresence.NO_WATER_SEEN, when, False) is not None
    assert _presence_line(WaterPresence.UNCERTAIN, when, False) is None
    assert _presence_line(WaterPresence.UNKNOWN, when, False) is None


def test_presence_line_is_dated():
    """One observation from one overpass. It must carry its date or it reads as
    a live status."""
    from datetime import datetime, timezone

    from app.services.i18n import _presence_line

    when = datetime(2026, 9, 14, tzinfo=timezone.utc)
    assert "14/09" in _presence_line(WaterPresence.WATER_SEEN, when, english=False)
    assert "14 Sep" in _presence_line(WaterPresence.WATER_SEEN, when, english=True)
    # No date available: still hedged, never presented as now.
    assert "hivi karibuni" in _presence_line(WaterPresence.WATER_SEEN, None, english=False)
