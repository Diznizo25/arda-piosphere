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
import math
import os
import tempfile
from dataclasses import dataclass, field

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.io import MemoryFile
from shapely import contains_xy
from shapely.geometry import shape

from app.services import forage
from app.services.forage import I_BSI, I_NDVI, I_SATVI, ForageClass
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
    #: Pixels falling inside the ring polygon. This is what the old
    #: `valid_pixel_count` actually counted, under a name that implied data.
    in_ring_count: int
    total_pixel_count: int
    #: Real finite-value count per band. Previously computed inside the loop and
    #: discarded, which is why nothing could detect a clouded-out read.
    valid_counts: dict[str, int] = field(default_factory=dict)
    #: Pixels with usable data in EVERY band the classifier needs.
    classified_count: int = 0
    #: Share of classifiable pixels per forage class.
    class_fractions: dict[ForageClass, float] = field(default_factory=dict)

    @property
    def coverage_ratio(self) -> float:
        """Share of in-ring pixels carrying usable data in every classifier band.

        The old implementation returned in_ring / bounding-box, which is pi/4 for
        any circle: measured at four radii over a 60%-clouded raster it returned
        0.717, 0.775, 0.765, 0.775 — the same numbers it would return under a
        clear sky. It could not detect the one failure it existed to catch.
        """
        return self.classified_count / self.in_ring_count if self.in_ring_count else 0.0

    @property
    def usable_fraction(self) -> float | None:
        """Share of classifiable pixels an animal can graze, or None if unreadable."""
        if not self.class_fractions:
            return None
        return (self.class_fractions[ForageClass.GREEN_GROWING]
                + self.class_fractions[ForageClass.DRY_FORAGE])


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
            in_ring_count=0,
            total_pixel_count=0,
        )

    xs = transform.c + (np.arange(c0, c1) + 0.5) * transform.a
    ys = transform.f + (np.arange(r0, r1) + 0.5) * transform.e
    X, Y = np.meshgrid(xs, ys)
    mask = contains_xy(geom, X, Y)

    means: dict[str, float] = {}
    valid_counts: dict[str, int] = {}
    columns: dict[int, np.ndarray] = {}
    for i in range(out.shape[0]):
        band_name = BAND_NAMES[i] if i < len(BAND_NAMES) else f"band_{i + 1}"
        data = out[i][r0:r1, c0:c1][mask]
        # Fill rather than compress: the per-band vectors must stay ALIGNED so
        # the three classifier bands can be read pixel-by-pixel. compressed()
        # drops a different set of positions from each band, which would
        # silently classify NDVI from one pixel against SATVI from another.
        data = np.ma.filled(data, np.nan) if np.ma.isMaskedArray(data) else np.asarray(data)
        data = data.ravel()
        columns[i] = data
        finite = data[np.isfinite(data)]
        means[band_name] = float(finite.mean()) if finite.size else float("nan")
        valid_counts[band_name] = int(finite.size)

    # Classify the ring pixel by pixel, using the same function the map uses.
    # This is what lets the advisory report a DISTRIBUTION instead of the class
    # of a mean — a ring that is 17% green riverine strip and 83% bare averages
    # to "bare", a label that describes no pixel in it.
    classes = np.array([], dtype=np.uint8)
    if out.shape[0] > max(I_NDVI, I_SATVI, I_BSI):
        classes = forage.classify_array(
            columns[I_NDVI], columns[I_SATVI], columns[I_BSI])

    return ZoneStats(
        means=means,
        in_ring_count=int(mask.sum()),
        total_pixel_count=int(mask.size),
        valid_counts=valid_counts,
        classified_count=int((classes != ForageClass.NODATA).sum()) if classes.size else 0,
        class_fractions=forage.class_fractions(classes) if classes.size else {},
    )


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
                    h, w = src.height, src.width
                    if is_overview:
                        factor = max(1, math.ceil(max(h, w) / 1024))
                        out = src.read(
                            out_shape=(src.count, max(1, h // factor), max(1, w // factor)),
                            resampling=Resampling.average,
                            masked=True,
                        )
                        transform = src.transform * src.transform.scale(factor)
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
                h, w = src.height, src.width
                factor = max(1, math.ceil(max(h, w) / 1024))
                out = src.read(
                    out_shape=(src.count, max(1, h // factor), max(1, w // factor)),
                    resampling=Resampling.average,
                    masked=True,
                )
                transform = src.transform * src.transform.scale(factor)
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


def read_point_indices(water_source_id: str, lon: float, lat: float) -> dict[str, float] | None:
    """Index values at ONE point — the pixel a herder is standing on.

    This is for labelling, not for advice. When a herder reports "the grazing
    here is poor", the trainable label is the index vector at *their* location
    paired with that report, not the mean over a 25 km ring that mostly
    describes somewhere else.

    Returns None when the COG is unavailable or the point falls outside it.
    """
    res = read_overview_array(water_source_id, max_dim=1024)
    if res is None:
        return None
    arr, transform = res

    col = int((lon - transform.c) / transform.a)
    row = int((lat - transform.f) / transform.e)
    if not (0 <= row < arr.shape[1] and 0 <= col < arr.shape[2]):
        return None

    out: dict[str, float] = {}
    for i in range(min(arr.shape[0], len(BAND_NAMES))):
        v = float(arr[i][row, col])
        if math.isfinite(v):
            out[BAND_NAMES[i]] = v
    return out or None


def read_overview_array(water_source_id: str, bands: list[int] | None = None,
                        max_dim: int = 512) -> tuple[np.ndarray, object] | None:
    """Fetch the 8x overview COG for a water source and return (bands, transform).

    Returns None if no COG/overview exists (e.g. the point has not been built).
    The band order is NDVI, NDRE, SATVI, BSI, NDMI, NDWI, VCI, GSW (all 8 bands
    by default, rasterio 1-based indexes — pass `bands=[1,3,4]` for just
    NDVI/SATVI/BSI).

    Memory-safe: the read is decimated so the largest dimension never exceeds
    `max_dim` (default 512), bounding RAM on small instances. The returned
    transform is adjusted for the decimation so geolocation stays correct.
    """
    from app.config import get_settings

    settings = get_settings()
    try:
        client = get_s3_client()
        obj = client.get_object(
            Bucket=settings.r2_bucket_name, Key=cog_overview_key(water_source_id)
        )
        data = obj["Body"].read()
    except Exception:  # noqa: BLE001
        return None
    try:
        with MemoryFile(data) as memfile:
            with memfile.open() as src:
                count = len(bands) if bands else src.count
                h, w = src.height, src.width
                factor = max(1, math.ceil(max(h, w) / max_dim))
                if factor == 1 and bands is None:
                    out = src.read()
                else:
                    out = src.read(
                        indexes=bands,
                        out_shape=(count, max(1, h // factor), max(1, w // factor)),
                        resampling=Resampling.average,
                    )
                transform = src.transform * src.transform.scale(factor)
        return out, transform
    except Exception:  # noqa: BLE001
        return None


