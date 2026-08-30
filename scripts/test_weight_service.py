"""Unit-style sanity checks for the weight service."""
from __future__ import annotations

import sys

sys.path.insert(0, ".")
from app.services import weight  # noqa: E402


def main() -> None:
    checks = [
        ("cattle", 165, "adult", 280.0, 350.0),
        ("cattle", 165, "young", 252.0, 315.0),
        ("goat", 65, "adult", 15.0, 25.0),
        ("sheep", 75, "adult", 28.0, 40.0),
        ("camel", 200, "adult", 400.0, 600.0),
    ]
    for species, girth, age, lo, hi in checks:
        est = weight.estimate_weight(species, girth, age)
        ok = lo <= est.weight_kg <= hi
        print(f"{species} {girth}cm {age}: {est.weight_kg} kg  [{'OK' if ok else 'OUT OF RANGE'}]")
        if not ok:
            raise SystemExit(1)

    ok, err = weight.validate_girth("cattle", 45)
    print("cattle 45cm valid:", ok, "|", err)
    assert not ok

    herd = weight.estimate_herd("cattle", 12, [150, 165, 170])
    print(f"herd: total={herd.estimated_total_kg} kg range=[{herd.low_estimate_kg}, {herd.high_estimate_kg}]")
    assert herd.herd_count == 12 and herd.sample_size == 3
    assert herd.low_estimate_kg <= herd.estimated_total_kg <= herd.high_estimate_kg

    print("\nAll weight checks passed.")


if __name__ == "__main__":
    main()
