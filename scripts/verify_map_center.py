"""Verify the rendered map's geographic center matches the herder location.

Renders the map for a herder at Isiolo town and reports the geographic bounds
of the image (derived from the render math) + the OSM tiles fetched.
"""
from __future__ import annotations

import math
import sys

sys.path.insert(0, ".")
from app.services import map_renderer  # noqa: E402


def merc_x_to_lon(x: float) -> float:
    return x * 180.0 / 20037508.34


def merc_y_to_lat(y: float) -> float:
    return math.degrees(math.atan(math.sinh(y * math.pi / 20037508.34)))


def main() -> None:
    ws_lon, ws_lat = 37.5725, 0.3525   # d6734528 (Isiolo town)
    herder_lon, herder_lat = 37.58, 0.35

    png = map_renderer.render_rings_png(
        "d6734528-8e63-4c69-8d02-a6e5a004b0f5", herder_lon, herder_lat
    )
    print(f"rendered {len(png)} bytes")

    max_radius_km = 25.0
    zoom = map_renderer._compute_zoom(herder_lat, max_radius_km)
    mpp = 156543.03392 * math.cos(math.radians(herder_lat)) / (2 ** zoom)
    cx = map_renderer._mercator_x(herder_lon)
    cy = map_renderer._mercator_y(herder_lat)
    half = map_renderer.IMG_SIZE / 2 * mpp
    west, east = cx - half, cx + half
    north, south = cy + half, cy - half

    print(f"zoom={zoom} mpp={mpp:.1f}")
    print(f"herder: lat={herder_lat} lon={herder_lon}")
    print(f"image center -> lat={merc_y_to_lat(cy):.4f} lon={merc_x_to_lon(cx):.4f}")
    print(f"image north edge lat={merc_y_to_lat(north):.4f}")
    print(f"image south edge lat={merc_y_to_lat(south):.4f}")

    # The user says the map shows Tharaka Nithi (~37.7E, -0.15S).
    tharaka_lat, tharaka_lon = -0.15, 37.7
    ty = map_renderer._mercator_y(tharaka_lat)
    tx = map_renderer._mercator_x(tharaka_lon)
    print(f"Tharaka Nithi ({tharaka_lon},{tharaka_lat}) px: "
          f"x={(tx-west)/mpp:.0f} y={(north-ty)/mpp:.0f} (image is 1024x1024)")


if __name__ == "__main__":
    main()
