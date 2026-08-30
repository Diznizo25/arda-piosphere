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
import os
import tempfile
from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.io import MemoryFile
from shapely import contains_xy
from shapely.geometry import shape

from app.services.storage import (
    cog_key,
    cog_overview_key,
    cog_overview_uri,
    cog_uri,
    get_s3_client,
)

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

    The contains_xy mask is computed only over the polygon's bounding box
    (rings are a small fraction of the raster extent) for a big speedup.
    """
    height, width = out.shape[1], out.shape[2]
    minx, miny, maxx, maxy = geom.bounds
    c0 = max(0, int((minx - transform.c) / transform.a))
    c1 = min(width, int((maxx - transform.c) / transform.a) + 1)
    r0 = max(0, int((maxy - transform.f) / transform.e))
    r1 = min(height, int((miny - transform.f) / transform.e) + 1)

    if c1 <= c0 or r1 <= r0:
        # No overlap between the raster grid and the polygon.
        return ZoneStats(
            means={name: float("nan") for name in BAND_NAMES[: out.shape[0]]},
            valid_pixel_count=0,
            total_pixel_count=0,
        )

    xs = transform.c + (np.arange(c0, c1) + 0.5) * transform.a
    ys = transform.f + (np.arange(r0, r1) + 0.5) * transform.e
    X, Y = np.meshgrid(xs, ys)
    mask = contains_xy(geom, X, Y)

    means: dict[str, float] = {}
    total = mask.size
    valid = int(mask.sum())
    for i in range(out.shape[0]):
        band_name = BAND_NAMES[i] if i < len(BAND_NAMES) else f"band_{i + 1}"
        data = out[i][r0:r1, c0:c1][mask]
        data = data.compressed() if hasattr(data, "compressed") else np.asarray(data).flatten()
        data = data[np.isfinite(data)]
        means[band_name] = float(np.mean(data)) if data.size else float("nan")

    return ZoneStats(means=means, valid_pixel_count=valid, total_pixel_count=total)


def read_zone_stats(water_source_id: str, species_zone_geojson: str) -> ZoneStats:
    """Open the water point's COG (preferring the 8x overview), clip to the
    species-specific ring polygon, and return per-band means over valid pixels.

    Read sources, in order of preference:
      1. public/CDN base URL via /vsicurl/  (when cog_public_base_url is set)
      2. R2 via GDAL /vsis3/                 (only attempted when the GDAL S3
         endpoint env var AWS_S3_ENDPOINT/AWS_ENDPOINT_URL is present)
      3. R2 via boto3 + MemoryFile/tempfile  (always works with R2_* credentials)
    """
    from app.config import get_settings

    geom = shape(json.loads(species_zone_geojson))
    settings = get_settings()
    gdal_s3_configured = bool(
        os.environ.get("AWS_S3_ENDPOINT") or os.environ.get("AWS_ENDPOINT_URL")
    )

    if settings.cog_public_base_url or gdal_s3_configured:
        with rasterio.Env(
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
        ):
            for uri in (cog_overview_uri(water_source_id), cog_uri(water_source_id)):
                try:
                    src = rasterio.open(uri)
                except Exception:  # noqa: BLE001  (object missing/unreachable -> try next)
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

    # No HTTP/GDAL read possible (or GDAL not configured for R2) — read the
    # object straight from R2 with the boto3 client.
    return _read_via_s3(water_source_id, geom)


def _read_via_s3(water_source_id: str, geom) -> ZoneStats:
    """Read the overview (or decimated full COG) using boto3 + R2 credentials.

    The 8x overview is small (~8-12MB) so it is fetched fully into a MemoryFile.
    The full COG (~500MB) is streamed to a temp file first to bound memory.
    """
    from app.config import get_settings

    settings = get_settings()
    client = get_s3_client()
    bucket = settings.r2_bucket_name

    # Overview first: it is small, fast, and the preferred read source.
    try:
        obj = client.get_object(Bucket=bucket, Key=cog_overview_key(water_source_id))
        data = obj["Body"].read()
        with MemoryFile(data) as memfile:
            with memfile.open() as src:
                out = src.read(masked=True)
                transform = src.transform
        return _read_band_means(out, transform, geom)
    except Exception:  # noqa: BLE001  (no overview -> try full COG below)
        pass

    # Full-COG fallback: stream to a temp file, then decimate the read.
    with tempfile.NamedTemporaryFile(suffix=".tif") as tmp:
        try:
            client.download_fileobj(bucket, cog_key(water_source_id), tmp)
        except Exception:  # noqa: BLE001
            raise RuntimeError(f"no readable COG for water_source_id={water_source_id}")
        tmp.flush()
        with rasterio.open(tmp.name) as src:
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


