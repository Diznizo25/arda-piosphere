"""Tier-1 tests: watering-interval reach + three grazing zones + advisory wording.

Pure config/i18n tests (no DB, no satellite reads — nothing recomputed).
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.config import get_species_rings  # noqa: E402
from app.services.advisory_logic import ForageCondition, WaterReliability  # noqa: E402
from app.services.i18n import format_advisory_message  # noqa: E402

rings = get_species_rings()


def approx(a, b, tol=0.05):
    assert abs(a - b) <= tol, f"{a} !~ {b}"


# effective reach by watering interval (daily = base ring; every_2_3 wider; cap 25)
approx(rings.effective_radius_km("cattle", "daily"), 7.0)
approx(rings.effective_radius_km("cattle", "every_2_3_days"), 9.8)
approx(rings.effective_radius_km("shoat", "every_2_3_days"), 14.3, 0.2)
approx(rings.effective_radius_km("camel", "every_2_3_days"), 25.0)  # capped at compute
print("effective radius by interval: OK")

# grazing-zone classification (fractions of the EFFECTIVE ring)
assert rings.grazing_zone(3.0, "cattle", "daily") == "comfortable"   # < 5.6 (0.8*7)
assert rings.grazing_zone(6.0, "cattle", "daily") == "far"           # >=0.8R, <1.0R
assert rings.grazing_zone(7.0, "cattle", "daily") == "critical"
assert rings.grazing_zone(8.0, "cattle", "every_2_3_days") == "far"  # 8/9.8 = 0.82
assert rings.grazing_zone(9.8, "cattle", "every_2_3_days") == "critical"
assert rings.grazing_zone(6.0, "cattle", "daily") != "comfortable"
print("grazing-zone classification: OK")

# advisory wording: far/critical add the warning, comfortable does not
common = dict(condition=ForageCondition.DRY_FORAGE_AVAILABLE, seasonally_normal=True,
              curing_stage_note=None, water_reliability=WaterReliability.RELIABLE)
msg_safe = format_advisory_message("swahili", "cattle", 4.0, grazing_zone="comfortable",
                                  effective_radius_km=7.0, **common)
msg_far = format_advisory_message("swahili", "cattle", 6.0, grazing_zone="far",
                                  effective_radius_km=7.0, **common)
msg_crit = format_advisory_message("english", "cattle", 7.1, grazing_zone="critical",
                                   effective_radius_km=7.0, **common)
assert "hatari" not in msg_safe
assert "⚠️" in msg_far and "ukingo" in msg_far
assert "🚨" in msg_crit and "safe limit" in msg_crit
print("zone advisory wording (swa+eng): OK")

print("\nTier-1 config/i18n tests OK.")
