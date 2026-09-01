"""Show what the water-point confirmation list looks like now (names + type + direction)."""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.routers.whatsapp import _source_label, _water_type_swa  # noqa: E402
from app.services import water_reach  # noqa: E402


def main() -> None:
    nearby = water_reach.list_nearby_water_sources(37.58, 0.35, limit=8)
    print(f"{len(nearby)} nearby water points:")
    for i, n in enumerate(nearby):
        label = _source_label(n, "swahili")
        wtype = _water_type_swa(n, "swahili")
        print(f"  {i + 1}. {label} — {wtype}, {n['distance_km']} km {n['direction_swa']}")
    # show the same in English
    print("\nEnglish:")
    for i, n in enumerate(nearby[:4]):
        label = _source_label(n, "english")
        wtype = _water_type_swa(n, "english")
        print(f"  {i + 1}. {label} — {wtype}, {n['distance_km']} km {n['direction_swa']}")


if __name__ == "__main__":
    main()
