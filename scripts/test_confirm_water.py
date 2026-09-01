"""Smoke-test the herder water-point confirmation plumbing against the DB."""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.services import pastoralists, water_reach  # noqa: E402


def main() -> None:
    # 1) nearby named list (the confirmation options)
    nearby = water_reach.list_nearby_water_sources(37.58, 0.35, limit=10)
    print(f"nearby water sources: {len(nearby)}")
    for n in nearby[:4]:
        print("  ", n["ward"], f"{n['distance_km']} km", n["source_type"])
    assert nearby, "expected at least one nearby water source"
    assert "water_source_id" in nearby[0] and "distance_km" in nearby[0]

    # 2) remember a herder's confirmed water point (use a test phone)
    phone = "+000-water-test"
    try:
        pastoralists.upsert_pastoralist(phone)
        pastoralists.set_water_source(phone, nearby[0]["water_source_id"])
        got = pastoralists.get_pastoralist(phone)
        print(f"confirmed water_source_id: {got.water_source_id}")
        assert got.water_source_id == nearby[0]["water_source_id"]
        ws = pastoralists.get_water_source(phone)
        print("get_water_source:", ws)
        assert ws and ws["ward"] == nearby[0]["ward"]
    finally:
        pastoralists.delete_pastoralist(phone)
    print("water-point confirmation plumbing OK")


if __name__ == "__main__":
    main()
