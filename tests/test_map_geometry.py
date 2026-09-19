"""Map geometry helpers. Ported from scripts/test_pasture_logic.py.

These are the calculations behind "the water is 4.2 km to the north-east", so
an error here sends a herd the wrong way.
"""
from __future__ import annotations

import pytest

from app.services import map_renderer


@pytest.mark.parametrize("dlat,dlon,expected", [
    (1, 0, 0),      # due north
    (0, 1, 90),     # due east
    (-1, 0, 180),   # due south
    (0, -1, 270),   # due west
])
def test_bearing_cardinal_directions(dlat, dlon, expected):
    assert abs(map_renderer._bearing_deg(0, 0, dlat, dlon) - expected) < 1


@pytest.mark.parametrize("deg,label", [
    (0, "N"), (45, "NE"), (90, "E"), (135, "SE"),
    (180, "S"), (225, "SW"), (270, "W"), (315, "NW"),
    (350, "N"),   # wraps back round to north
])
def test_compass_labels(deg, label):
    assert map_renderer._compass_label(deg) == label


@pytest.mark.parametrize("lon,lat", [(37.58, 0.35), (36.99, 0.5854), (37.5, -0.1)])
def test_mercator_roundtrip(lon, lat):
    """Projection is closed-form here (no pyproj), so the inverse must be exact."""
    lon2 = map_renderer._mercator_x_inv(map_renderer._mercator_x(lon))
    lat2 = map_renderer._mercator_y_inv(map_renderer._mercator_y(lat))
    assert abs(lon2 - lon) < 1e-6
    assert abs(lat2 - lat) < 1e-6
