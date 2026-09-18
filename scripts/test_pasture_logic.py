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
    """Delegates to the shared classifier (app/services/forage.py).

    This used to duplicate the renderer's sequential-overwrite logic inline, so
    it asserted the bug rather than the renderer: a green pixel with low SATVI
    or high BSI was repainted "bare". See docs/adr/002-one-classifier.md and
    tests/test_forage.py, which is now the real home for these checks.
    """
    import numpy as np

    from app.services.forage import ForageClass, classify_array

    ndvi = np.array([[0.5, 0.1, 0.1], [0.2, 0.15, 0.3], [0.1, np.nan, 0.3]])
    satvi = np.array([[0.1, 0.3, 0.02], [0.2, 0.13, 0.1], [0.4, 0.1, 0.3]])
    bsi = np.array([[0.2, 0.05, 0.3], [0.08, 0.2, 0.1], [0.1, 0.2, 0.3]])
    classes = classify_array(ndvi.ravel(), satvi.ravel(), bsi.ravel()).reshape(ndvi.shape)

    assert classes[0, 0] == ForageClass.GREEN_GROWING, classes   # NDVI 0.5 wins
    assert classes[0, 1] == ForageClass.DRY_FORAGE, classes      # cured grass
    assert classes[0, 2] == ForageClass.BARE_DEGRADED, classes   # BSI 0.3
    assert classes[1, 0] == ForageClass.DRY_FORAGE, classes
    assert classes[2, 1] == ForageClass.NODATA, classes          # nan
    print("classification OK (shared classifier)")


def main() -> None:
    test_bearing()
    test_compass()
    test_mercator_roundtrip()
    test_classification()
    print("\nAll pasture-map logic checks passed.")


if __name__ == "__main__":
    main()
