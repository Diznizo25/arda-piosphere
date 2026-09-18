"""
Golden-file tests for the text a herder actually receives.

Every scenario below renders to a committed file under tests/golden/. A diff in
one of those files is a deliberate, reviewable change to what a pastoralist
reads on their phone — which is the point: wording changes here have reached
people in the field without anyone seeing a diff.

Regenerate deliberately, never casually:

    UPDATE_GOLDEN=1 pytest tests/test_advisory_message.py

then read the diff before committing it.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from app.services.advisory_logic import ForageCondition, WaterReliability
from app.services.i18n import format_advisory_message
from tests.conftest import GOLDEN_DIR

NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)
FRESH = NOW - timedelta(days=4)
STALE = NOW - timedelta(days=47)

# Distribution of a ring that is mostly bare with a green riverine strip — the
# case that used to report "eneo tupu, malisho hafifu" (bare ground, very little
# pasture) while a fifth of it was walkable grazing.
BIMODAL = {"green_growing": 0.17, "dry_forage": 0.0,
           "bare_degraded": 0.83, "uncertain": 0.0}
MOSTLY_DRY_FORAGE = {"green_growing": 0.04, "dry_forage": 0.71,
                     "bare_degraded": 0.25, "uncertain": 0.0}
MOSTLY_BARE = {"green_growing": 0.01, "dry_forage": 0.06,
               "bare_degraded": 0.93, "uncertain": 0.0}

BASE = dict(
    # Pinned clock: a golden file must not drift because the date rolled over.
    now=NOW,
    species="cattle",
    distance_km=4.2,
    curing_stage_note=None,
    water_reliability=WaterReliability.RELIABLE,
    grazing_zone="comfortable",
    effective_radius_km=7.0,
    dry_harsh=False,
)

SCENARIOS: dict[str, dict] = {
    "bimodal_green_strip_swa": {
        **BASE, "language": "swahili",
        "condition": ForageCondition.BARE_DEGRADED, "seasonally_normal": True,
        "class_fractions": BIMODAL,
        "patch_bearing_deg": 48.0, "patch_distance_km": 6.1,
        "observed_at": FRESH,
    },
    "bimodal_green_strip_eng": {
        **BASE, "language": "english",
        "condition": ForageCondition.BARE_DEGRADED, "seasonally_normal": True,
        "class_fractions": BIMODAL,
        "patch_bearing_deg": 48.0, "patch_distance_km": 6.1,
        "observed_at": FRESH,
    },
    "dry_forage_available_swa": {
        **BASE, "language": "swahili",
        "condition": ForageCondition.DRY_FORAGE_AVAILABLE, "seasonally_normal": True,
        "curing_stage_note": "still_curing",
        "class_fractions": MOSTLY_DRY_FORAGE,
        "patch_bearing_deg": 200.0, "patch_distance_km": 2.4,
        "observed_at": FRESH,
    },
    "bare_and_abnormal_swa": {
        **BASE, "language": "swahili",
        "condition": ForageCondition.BARE_DEGRADED, "seasonally_normal": False,
        "class_fractions": MOSTLY_BARE, "dry_harsh": True,
        "patch_bearing_deg": 310.0, "patch_distance_km": 18.5,
        "observed_at": FRESH,
    },
    # seasonally_normal=None means VCI was unreadable. The message must NOT say
    # "worse than usual for this season" — the old code reached that line via
    # `nan >= 35` being False.
    "bare_season_unknown_swa": {
        **BASE, "language": "swahili",
        "condition": ForageCondition.BARE_DEGRADED, "seasonally_normal": None,
        "class_fractions": MOSTLY_BARE,
        "patch_bearing_deg": None, "patch_distance_km": None,
        "observed_at": FRESH,
    },
    "stale_observation_swa": {
        **BASE, "language": "swahili",
        "condition": ForageCondition.DRY_FORAGE_AVAILABLE, "seasonally_normal": True,
        "class_fractions": MOSTLY_DRY_FORAGE,
        "patch_bearing_deg": 90.0, "patch_distance_km": 3.0,
        "observed_at": STALE,
    },
    # No distribution available (e.g. an old cached path): the message must fall
    # back to the single-label wording rather than omitting the forage line.
    "no_distribution_fallback_swa": {
        **BASE, "language": "swahili",
        "condition": ForageCondition.GREEN_GROWING, "seasonally_normal": True,
        "class_fractions": None,
        "patch_bearing_deg": None, "patch_distance_km": None,
        "observed_at": None,
    },
    "far_from_water_warns_swa": {
        **BASE, "language": "swahili", "distance_km": 5.0,
        "grazing_zone": "far",
        "condition": ForageCondition.DRY_FORAGE_AVAILABLE, "seasonally_normal": True,
        "class_fractions": MOSTLY_DRY_FORAGE,
        "patch_bearing_deg": 12.0, "patch_distance_km": 1.8,
        "observed_at": FRESH,
    },
}


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_advisory_message_matches_golden(scenario):
    rendered = format_advisory_message(**SCENARIOS[scenario])
    path = GOLDEN_DIR / f"advisory_{scenario}.txt"

    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")
        pytest.skip(f"golden file regenerated: {path.name}")

    assert path.exists(), (
        f"missing golden file {path.name} — run UPDATE_GOLDEN=1 pytest and review the diff")
    assert rendered == path.read_text(encoding="utf-8").rstrip("\n")


# --- properties that must hold regardless of wording -------------------------

def test_unknown_season_never_claims_abnormal():
    msg = format_advisory_message(**SCENARIOS["bare_season_unknown_swa"])
    assert "mbaya zaidi ya kawaida" not in msg
    assert "ya kawaida kwa msimu huu" not in msg


def test_a_green_strip_is_mentioned_even_when_the_ring_is_mostly_bare():
    """The whole point of reporting a distribution instead of a mean."""
    msg = format_advisory_message(**SCENARIOS["bimodal_green_strip_swa"])
    assert "eneo tupu" not in msg, "must not call a ring with 17% grazing 'bare ground'"
    assert "Malisho bora" in msg, "must point the herder at the strip"
    assert "Kaskazini-Mashariki" in msg


def test_stale_reading_says_so():
    msg = format_advisory_message(**SCENARIOS["stale_observation_swa"])
    assert "siku 47" in msg


def test_a_far_away_patch_is_not_offered_as_walking_guidance():
    """Beyond MAX_PATCH_GUIDANCE_KM it is a migration, not a walk."""
    msg = format_advisory_message(
        **{**SCENARIOS["dry_forage_available_swa"], "patch_distance_km": 120.0})
    assert "Malisho bora" not in msg


def test_a_patch_beyond_the_herders_reach_is_not_offered():
    """A cattle herder's ring is 7 km. Pointing them at grazing 18.5 km away
    reads as guidance but is a two-day trek — the advisory stays silent."""
    msg = format_advisory_message(**SCENARIOS["bare_and_abnormal_swa"])
    assert "Malisho bora" not in msg

    # ...whereas a camel herder, whose reach is 25 km, IS told about it.
    camel = {**SCENARIOS["bare_and_abnormal_swa"],
             "species": "camel", "effective_radius_km": 25.0}
    assert "Malisho bora" in format_advisory_message(**camel)
