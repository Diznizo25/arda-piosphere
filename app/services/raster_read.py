"""
Windowed rasterio reads from the per-water-point COG (architecture principle
#2: rasterio does live windowed reads at request time; GEE never runs live).

Each COG holds the FULL outer (camel) ring stack. To answer for cattle/shoat,
we read the same COG but mask/clip the pixel window to that narrower species
polygon before averaging — "tag results by which inner ring they fall within
at read time rather than computing three times" per the architecture spec.

Reads prefer the tiny 8x block-averaged OVERVIEW object (cogs/<id>/indices_ov8.tif,
~12MB) — zone means are statistically unchanged by 8x averaging, and it keeps
the read path fast and memory-safe on small instances. Falls back to a 4x
decimated read of the full ~500MB COG when no overview object exists yet.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.enums import Resampling
from shapely import contains_xy
from shapely.geometry import shape

from app.services.storage import cog_overview_uri, cog_uri

BAND_NAMES = ["NDVI", "NDRE", "SATVI", "BSI", "NDMI", "NDWI", "VCI", "GSW_MONTHLY_RECURRENCE"]

# Decimation for the full-COG fallback path (no overview object available).
DECIMATE = 4


@dataclass
class ZoneStats:
    means: dict[str, float]
    valid_pixel_count: int
    total_pixel_count: int

    @property
    def coverage_ratio(self) -> float:
        return self.valid_pixel_count / self.total_pixel_count if self.total_pixel_count else 0.0


def _read_band_means(out: np.ndarray, transform, geom) -> ZoneStats:
    """Average each band over the pixels inside the ring polygon.

    `out` is (bands, height, width) from a rasterio read (masked=True so nodata
    pixels are masked out); `transform` locates those pixels on the map grid.
    """
    height, width = out.shape[1], out.shape[2]
    xs = transform.c + (np.arange(width) + 0.5) * transform.a
    ys = transform.f + (np.arange(height) + 0.5) * transform.e
    X, Y = np.meshgrid(xs, ys)
    mask = contains_xy(geom, X, Y)

    means: dict[str, float] = {}
    total = mask.size
    valid = int(mask.sum())
    for i in range(out.shape[0]):
        band_name = BAND_NAMES[i] if i < len(BAND_NAMES) else f"band_{i + 1}"
        data = out[i][mask]
        data = np.asarray(data).compressed() if hasattr(data, "compressed") else np.asarray(data).flatten()
        data = data[np.isfinite(data)]
        means[band_name] = float(np.mean(data)) if data.size else float("nan")

    return ZoneStats(means=means, valid_pixel_count=valid, total_pixel_count=total)


def read_zone_stats(water_source_id: str, species_zone_geojson: str) -> ZoneStats:
    """Open the water point's COG (preferring the 8x overview), clip to the
    species-specific ring polygon, and return per-band means over valid pixels."""
    geom = shape(json.loads(species_zone_geojson))

    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
    ):
        for uri in (cog_overview_uri(water_source_id), cog_uri(water_source_id)):
            try:
                src = rasterio.open(uri)
            except Exception:  # noqa: BLE001  (object missing -> try next)
                continue
            with src:
                is_overview = uri.endswith("_ov8.tif")
                if is_overview:
                    out = src.read(masked=True)
                    transform = src.transform
                else:
                    out_h = max(1, src.height // DECIMATE)
                    out_w = max(1, src.width // DECIMATE)
                    out = src.read(
                        out_shape=(src.count, out_h, out_w),
                        resampling=Resampling.average,
                        masked=True,
                    )
                    transform = src.transform * src.transform.scale(
                        src.width / out_w, src.height / out_h
                    )
                return _read_band_means(out, transform, geom)

    raise RuntimeError(f"no readable COG for water_source_id={water_source_id}")


