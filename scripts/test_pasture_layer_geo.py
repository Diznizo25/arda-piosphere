"""Geo-correctness of the interactive pasture layer (no DB / no R2 needed).

1. Row mapping: rasterio north-up GeoTIFF rows are lat = f + row*e with e
   NEGATIVE, so sampling must use row = (lat - f)/e. A sign slip here puts the
   pasture on the wrong side of the map (user-facing symptom: pasture "not
   distributed in the ring / weird half circle").  A synthetic stripe south of
   the water point must render in the image's southern rows.
2. Ring clip: pasture colour must never appear outside the species' effective
   ring radius (the drawn grazing zone) on the layer PNG.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from rasterio.io import MemoryFile  # noqa: E402

from app.services.map_renderer import (  # noqa: E402
    _mercator_x, _mercator_y, _mercator_x_inv, _mercator_y_inv,
    _rgba_classes, IMG_SIZE,
)

RES = 1e-3  # deg/px ~ 111 m
W, H = 200, 200
CENTER_LON, CENTER_LAT = 37.58, 0.35
f0 = CENTER_LAT + (H / 2) * RES   # north edge
c0 = CENTER_LON - (W / 2) * RES   # west edge
tr = rasterio.Affine(RES, 0.0, c0, 0.0, -RES, f0)  # standard north-up

rows = np.arange(H)
lats = f0 + rows * tr.e


def _frame_mpp():
    return (W * RES * 111000.0) / IMG_SIZE


def _make_classes(south_band=(0.30, 0.34)):
    """Class grid: green stripe just SOUTH of the water point."""
    r_lo = int(round((south_band[0] - f0) / tr.e))
    r_hi = int(round((south_band[1] - f0) / tr.e))
    gx0 = int(round((37.55 - c0) / tr.a))
    gx1 = int(round((37.61 - c0) / tr.a))
    data = np.full((1, H, W), 0, dtype=np.uint8)
    data[0, min(r_lo, r_hi):max(r_lo, r_hi) + 1, gx0:gx1 + 1] = 1
    with MemoryFile() as mf:
        with mf.open(driver="GTiff", height=H, width=W, count=1, dtype="uint8",
                     crs="EPSG:4326", transform=tr, nodata=0) as dst:
            dst.write(data)
        with mf.open() as src:
            out = src.read()
            return np.where(np.isnan(out[0]), 0, out[0]).astype(np.uint8)


def _render(classes, radius_km):
    mpp = _frame_mpp()
    half = IMG_SIZE / 2 * mpp
    west = _mercator_x(CENTER_LON) - half
    north = _mercator_y(CENTER_LAT) + half
    img = _rgba_classes(classes, tr, west, north, mpp,
                        center_lon=CENTER_LON, center_lat=CENTER_LAT,
                        radius_km=radius_km)
    return np.asarray(img), west, north, mpp


def main():
    classes = _make_classes()
    radius_km = 40.0  # no clip for the row-mapping check
    a, west, north, mpp = _render(classes, radius_km)
    green = (a[..., 0] == 34) & (a[..., 1] == 197) & (a[..., 2] == 94) & (a[..., 3] > 60)
    py, _ = np.nonzero(green)
    assert green.sum() > 10_000, "no green stripe rendered"
    img_row_lats = _mercator_y_inv(north - (np.arange(IMG_SIZE) + 0.5) * mpp)
    band = np.where((img_row_lats >= 0.30) & (img_row_lats <= 0.34))[0]
    overlap = len(set(py.tolist()) & set(band.tolist()))
    assert overlap > 0, f"row mapping wrong: green rows {py.min()}..{py.max()}, expected {band.min()}..{band.max()}"
    print(f"row mapping OK (stripe rows {py.min()}..{py.max()} overlap expected band {band.min()}..{band.max()})")

    # Ring clip: any species ring -> colour confined inside it.
    a, west, north, mpp = _render(classes, radius_km=20.0)
    alpha = a[..., 3] > 60
    px, py = np.nonzero(alpha)
    lons = _mercator_x_inv(west + (px.astype(float) + 0.5) * mpp)
    lats = _mercator_y_inv(north - (py.astype(float) + 0.5) * mpp)
    dlon = (lons - CENTER_LON) * 111.32 * np.cos(np.radians(CENTER_LAT))
    dlat = (lats - CENTER_LAT) * 110.57
    dist = np.hypot(dlon, dlat)
    assert alpha.sum() > 100
    assert dist.max() <= 21.0, f"colour escaped the ring (max {dist.max():.1f} km)"
    print(f"ring clip OK (coloured px {alpha.sum()}, max {dist.max():.1f} km inside the ring)")


if __name__ == "__main__":
    sys.exit(main())
