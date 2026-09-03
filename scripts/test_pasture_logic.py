"""Pure-logic checks for the pasture-map helpers (no DB needed)."""
from __future__ import annotations

import sys

sys.path.insert(0, ".")
from app.services import map_renderer  # noqa: E402


def test_bearing() -> None:
    # due north from (0,0) to (1,0) -> 0
    b = map_renderer._bearing_deg(0, 0, 1, 0)
    assert abs(b - 0) < 1, b
    # due east -> 90
    b = map_renderer._bearing_deg(0, 0, 0, 1)
    assert abs(b - 90) < 1, b
    # due south -> 180
    b = map_renderer._bearing_deg(0, 0, -1, 0)
    assert abs(b - 180) < 1, b
    # due west -> 270
    b = map_renderer._bearing_deg(0, 0, 0, -1)
    assert abs(b - 270) < 1, b
    print("bearing OK")


def test_compass() -> None:
    assert map_renderer._compass_label(0) == "N"
    assert map_renderer._compass_label(45) == "NE"
    assert map_renderer._compass_label(90) == "E"
    assert map_renderer._compass_label(135) == "SE"
    assert map_renderer._compass_label(180) == "S"
    assert map_renderer._compass_label(225) == "SW"
    assert map_renderer._compass_label(270) == "W"
    assert map_renderer._compass_label(315) == "NW"
    assert map_renderer._compass_label(350) == "N"
    print("compass OK")


def test_mercator_roundtrip() -> None:
    import math

    for lon, lat in [(37.58, 0.35), (36.99, 0.5854), (37.5, -0.1)]:
        x = map_renderer._mercator_x(lon)
        y = map_renderer._mercator_y(lat)
        lon2 = map_renderer._mercator_x_inv(x)
        lat2 = map_renderer._mercator_y_inv(y)
        assert abs(lon2 - lon) < 1e-6, (lon, lon2)
        assert abs(lat2 - lat) < 1e-6, (lat, lat2)
    print("mercator roundtrip OK")


def test_classification() -> None:
    import numpy as np

    t = __import__("app.config", fromlist=["get_advisory_thresholds"]).get_advisory_thresholds().vegetation
    ndvi = np.array([[0.5, 0.1, 0.1], [0.2, 0.15, 0.3], [0.1, np.nan, 0.3]])
    satvi = np.array([[0.1, 0.3, 0.02], [0.2, 0.13, 0.1], [0.4, 0.1, 0.3]])
    bsi = np.array([[0.2, 0.05, 0.3], [0.08, 0.2, 0.1], [0.1, 0.2, 0.3]])

    good = np.isfinite(ndvi) & np.isfinite(satvi) & np.isfinite(bsi)
    classes = np.full(ndvi.shape, 0, dtype=np.uint8)
    classes[good & (ndvi >= t["ndvi_green_threshold"])] = 1  # green
    classes[good & (ndvi < t["ndvi_green_threshold"])
            & (satvi >= t["satvi_dry_forage_threshold"]) & (bsi < t["bsi_high_threshold"])] = 2  # dry
    classes[good & (satvi < t["satvi_bare_threshold"])] = 3  # bare
    classes[good & (bsi >= t["bsi_high_threshold"])] = 3
    classes[good & (classes == 0)] = 4  # uncertain

    # (0,0): NDVI 0.5 -> green (1)
    assert classes[0, 0] == 1, classes
    # (0,1): NDVI 0.1, SATVI 0.3, BSI 0.05 -> dry (2)
    assert classes[0, 1] == 2, classes
    # (0,2): BSI 0.3 -> bare (3)
    assert classes[0, 2] == 3, classes
    # (1,0): SATVI 0.2, BSI 0.08 -> dry (2)
    assert classes[1, 0] == 2, classes
    # (2,1): nan -> nodata (0)
    assert classes[2, 1] == 0, classes
    print("classification OK")


def main() -> None:
    test_bearing()
    test_compass()
    test_mercator_roundtrip()
    test_classification()
    print("\nAll pasture-map logic checks passed.")


if __name__ == "__main__":
    main()
