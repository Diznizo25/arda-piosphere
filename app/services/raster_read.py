"""
Windowed rasterio reads from the per-water-point COG (architecture principle
#2: rasterio does live windowed reads at request time; GEE never runs live).

Each COG holds the FULL outer (camel) ring stack. To answer for cattle/shoat,
we read the same COG but mask/clip the pixel window to that narrower species
polygon before averaging — "tag results by which inner ring they fall within
at read time rather than computing three times" per the architecture spec.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.mask import mask as rio_mask

from app.services.storage import cog_uri

BAND_NAMES = ["NDVI", "NDRE", "SATVI", "BSI", "NDMI", "NDWI", "VCI", "GSW_MONTHLY_RECURRENCE"]


@dataclass
class ZoneStats:
    means: dict[str, float]
    valid_pixel_count: int
    total_pixel_count: int

    @property
    def coverage_ratio(self) -> float:
        return self.valid_pixel_count / self.total_pixel_count if self.total_pixel_count else 0.0


def read_zone_stats(water_source_id: str, species_zone_geojson: str) -> ZoneStats:
    """Open the water point's COG, clip to the species-specific ring polygon,
    and return per-band means over valid (unmasked) pixels only."""
    uri = cog_uri(water_source_id)
    geom = json.loads(species_zone_geojson)

    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
    ):
        with rasterio.open(uri) as src:
            out_image, _ = rio_mask(src, [geom], crop=True, filled=False)
            band_count = out_image.shape[0]

    means: dict[str, float] = {}
    total = out_image[0].size
    valid = int((~out_image[0].mask).sum()) if hasattr(out_image[0], "mask") else total

    for i in range(band_count):
        band_name = BAND_NAMES[i] if i < len(BAND_NAMES) else f"band_{i+1}"
        band = out_image[i]
        data = band.compressed() if hasattr(band, "compressed") else np.asarray(band).flatten()
        means[band_name] = float(np.mean(data)) if data.size else float("nan")

    return ZoneStats(means=means, valid_pixel_count=valid, total_pixel_count=total)
