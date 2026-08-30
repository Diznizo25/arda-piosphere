"""Validate COG data quality: per-band stats + plausibility check.

Reads each water point's local overview/merged COG (rasterio) and reports
per-band mean/min/max and valid-pixel coverage, then flags any band whose
values are outside scientifically plausible ranges (would indicate empty or
corrupt exports).

Usage:
  python scripts/validate_cog_data.py [--water-source ID]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# band -> (lower, upper) plausible range for the stacked-index COG
# (order matches build_stacked_image_for_month in gee_indices.py)
BAND_RANGES = {
    "NDVI": (-1.0, 1.0),
    "NDRE": (-1.0, 1.0),
    "SATVI": (-0.8, 0.8),
    "BSI": (-1.0, 1.0),
    "NDMI": (-1.0, 1.0),
    "NDWI": (-1.0, 1.0),
    "VCI": (0.0, 100.0),
    # JRC GSW monthly_recurrence is defined only where water has been observed
    # (has_observations=1), so ~1% coverage is expected and correct for ASALs.
    "GSW_MONTHLY_RECURRENCE": (0.0, 100.0),
}

# Minimum valid-pixel coverage per band. GSW is naturally sparse.
MIN_COVERAGE = {
    "GSW_MONTHLY_RECURRENCE": 0.001,
}


def band_names(count: int) -> list[str]:
    names = list(BAND_RANGES.keys())
    return (names + [f"band_{i + 1}" for i in range(len(names), count)])[:count]


def validate_cog(cog_path: Path) -> tuple[bool, list[str]]:
    """Return (ok, messages) for one COG using the 8x overview when present."""
    path = cog_path
    ov = cog_path.with_name("overview_8x.tif")
    if ov.exists():
        path = ov
    problems: list[str] = []
    with rasterio.open(str(path)) as src:
        names = band_names(src.count)
        meta = src.meta
        # Block window sampling: read overview in tiles to bound memory.
        profile = src.profile
        overviews = src.overviews(1)
        if overviews:
            band_data = src.read(out_shape=(src.count, src.height // 8, src.width // 8))
        else:
            band_data = src.read()
        for i, name in enumerate(names):
            band = band_data[i]
            valid = band[band != src.nodata] if src.nodata is not None else band
            import numpy as np

            valid = valid[np.isfinite(valid)]
            n = int(valid.size)
            if n == 0:
                problems.append(f"{name}: NO VALID DATA")
                continue
            mean = float(valid.mean())
            lo, hi = BAND_RANGES.get(name, (-1e9, 1e9))
            frac = n / max(1, int(band.size))
            min_cov = MIN_COVERAGE.get(name, 0.5)
            flag = ""
            if not (lo <= mean <= hi):
                flag = "  <-- OUT OF RANGE"
                problems.append(f"{name}: mean {mean:.3f} outside [{lo}, {hi}]")
            elif frac < min_cov:
                flag = "  <-- LOW COVERAGE"
                problems.append(f"{name}: valid coverage {frac:.0%}")
            print(
                f"    {name:<22} mean={mean:8.3f} min={float(valid.min()):8.3f} "
                f"max={float(valid.max()):8.3f} coverage={frac:.0%}{flag}"
            )
    return not problems, problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--water-source", help="Limit to one water source id")
    args = parser.parse_args()

    tile_root = Path("data/tiles")
    dirs = sorted(tile_root.iterdir()) if tile_root.exists() else []
    if args.water_source:
        dirs = [tile_root / args.water_source]

    all_ok = True
    for d in dirs:
        merged = d / "merged.tif"
        if not merged.exists():
            print(f"== {d.name[:8]}  (no merged.tif, skipping)")
            continue
        print(f"== {d.name}  [{merged.stat().st_size / 1e6:.1f} MB]")
        ok, problems = validate_cog(merged)
        if not ok:
            all_ok = False
            for p in problems:
                print(f"    PROBLEM: {p}")

    print(f"\nOverall: {'ALL GOOD' if all_ok else 'ISSUES FOUND'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
