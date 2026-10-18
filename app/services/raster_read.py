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
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path

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

# Decoded overviews, shared between requests (all callers treat them read-only).
# A 512 px / 3-band read is ~3 MB; cap the count AND the size so a big multi-band
# read is served fresh instead of pinning memory on a small instance.
_OVERVIEW_CACHE: "OrderedDict[tuple, tuple[np.ndarray, object]]" = OrderedDict()
_OVERVIEW_CACHE_MAX = 6
_OVERVIEW_CACHE_MAX_ELEMS = 4_000_000  # ~16 MB of float32 per entry
_OVERVIEW_CACHE_LOCK = threading.Lock()

# Water points whose overview object does not exist yet (a freshly pinned point
# waits for the weekly build). Remembered briefly so every map load does not pay
# two R2 round trips to rediscover it.
_NO_OVERVIEW_UNTIL: dict[str, float] = {}
_NO_OVERVIEW_TTL_S = 60.0

# Reads that a page must not wait for run here, so the download survives the
# request that started it.
_OVERVIEW_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="overview-read")


def _overview_cache_get(key: tuple):
    with _OVERVIEW_CACHE_LOCK:
        hit = _OVERVIEW_CACHE.get(key)
        if hit is not None:
            _OVERVIEW_CACHE.move_to_end(key)
        return hit


def _overview_cache_put(key: tuple, value: tuple[np.ndarray, object]) -> None:
    arr = value[0]
    if arr.size > _OVERVIEW_CACHE_MAX_ELEMS:
        return
    try:
        # Shared between requests: a write must fail loudly, not corrupt every
        # later map that reads the same buffer.
        arr.setflags(write=False)
    except ValueError:
        return
    with _OVERVIEW_CACHE_LOCK:
        _OVERVIEW_CACHE[key] = value
        _OVERVIEW_CACHE.move_to_end(key)
        while len(_OVERVIEW_CACHE) > _OVERVIEW_CACHE_MAX:
            _OVERVIEW_CACHE.popitem(last=False)


def clear_overview_cache() -> None:
    """Drop cached overviews (tests, or after a point is rebuilt by hand)."""
    with _OVERVIEW_CACHE_LOCK:
        _OVERVIEW_CACHE.clear()
    _NO_OVERVIEW_UNTIL.clear()


# The download itself is also kept ON DISK, because the in-memory cache lives
# inside one worker process: a fresh worker, or the second of several workers,
# otherwise pays the full ~8 MB fetch again (measured: 20-30 s on the deployed
# instance). The container filesystem is shared by its workers, and /tmp survives
# requests, so the expensive part happens once per container, not once per worker.
_OVERVIEW_DISK_DIR = Path(tempfile.gettempdir()) / "piosphere-overview-cache"
_OVERVIEW_DISK_MAX_AGE_S = 7 * 24 * 3600


def _overview_disk_files(water_source_id: str) -> list[Path]:
    try:
        return sorted(_OVERVIEW_DISK_DIR.glob(f"{water_source_id}-*.tif"))
    except OSError:
        return []


def _overview_disk_read(water_source_id: str, etag: str | None,
                        size: int | None) -> bytes | None:
    """The object we already downloaded, if it is still THIS object.

    With an ETag we accept only the exact copy (and the matching byte count);
    without one (R2 unreachable for a moment) we fall back to the newest copy on
    disk, which is at worst last week's picture — and the map stamps its own age.
    """
    paths = _overview_disk_files(water_source_id)
    if not paths:
        return None
    if etag:
        want = _OVERVIEW_DISK_DIR / f"{water_source_id}-{etag[:16]}.tif"
        try:
            if want.exists() and (size is None or want.stat().st_size == size):
                return want.read_bytes()
        except OSError:
            return None
        return None
    try:
        newest = max(paths, key=lambda p: p.stat().st_mtime)
        if time.time() - newest.stat().st_mtime < _OVERVIEW_DISK_MAX_AGE_S:
            return newest.read_bytes()
    except OSError:
        return None
    return None


def _overview_disk_write(water_source_id: str, etag: str, data: bytes) -> None:
    try:
        _OVERVIEW_DISK_DIR.mkdir(parents=True, exist_ok=True)
        path = _OVERVIEW_DISK_DIR / f"{water_source_id}-{etag[:16]}.tif"
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        for old in _overview_disk_files(water_source_id):
            if old != path:
                old.unlink(missing_ok=True)
    except OSError:
        pass  # read-only or full disk: the in-memory cache still works


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

    CACHED per (water point, bands, max_dim). The object is ~12 MB and one page
    needs the SAME window several times (ring statistics, pasture colours, the
    legend percentages, then /map/{id}/pasture.png); re-downloading it for each
    of those made a single /mapview request take ~14 s on the deployed instance
    — long enough that a herder on a phone saw the map with no rings at all.
    Missing overviews are never cached, so a freshly built point appears as soon
    as its object lands.
    """
    key = (water_source_id, tuple(bands) if bands else None, int(max_dim))
    hit = _overview_cache_get(key)
    if hit is not None:
        return hit
    if _NO_OVERVIEW_UNTIL.get(water_source_id, 0.0) > time.time():
        return None  # asked again a moment ago: the object still is not there
    res = _read_overview_array_uncached(water_source_id, bands, max_dim)
    if res is None:
        _NO_OVERVIEW_UNTIL[water_source_id] = time.time() + _NO_OVERVIEW_TTL_S
    else:
        _NO_OVERVIEW_UNTIL.pop(water_source_id, None)
        _overview_cache_put(key, res)
    return res


def read_overview_array_warm(water_source_id: str, bands: list[int] | None = None,
                             max_dim: int = 512,
                             timeout_s: float = 2.0) -> tuple[np.ndarray, object] | None:
    """read_overview_array, but never let a page wait for a cold download.

    On the deployed instance the 8 MB overview takes 20-30 s to arrive. A map page
    cannot sit on that: the rings are ready in milliseconds and must be shown. The
    read carries on in a background thread and lands in both caches, so the next
    view (and /map/{id}/pasture.png) answers from memory.
    """
    key = (water_source_id, tuple(bands) if bands else None, int(max_dim))
    hit = _overview_cache_get(key)
    if hit is not None:
        return hit
    if _NO_OVERVIEW_UNTIL.get(water_source_id, 0.0) > time.time():
        return None
    future = _OVERVIEW_POOL.submit(read_overview_array, water_source_id, bands, max_dim)
    try:
        return future.result(timeout=timeout_s)
    except FutureTimeout:
        return None
    except Exception:  # noqa: BLE001
        return None


def overview_exists(water_source_id: str) -> bool:
    """Is there an overview COG for this point at all? (HEAD only, no download.)

    Lets a map say "the satellite layer is here; its percentages are still being
    read" instead of either waiting ~28 s or claiming there is no pasture data.
    """
    if _NO_OVERVIEW_UNTIL.get(water_source_id, 0.0) > time.time():
        return False
    try:
        from app.config import get_settings

        get_s3_client().head_object(Bucket=get_settings().r2_bucket_name,
                                    Key=cog_overview_key(water_source_id))
        return True
    except Exception:  # noqa: BLE001
        return False


def _read_overview_array_uncached(water_source_id: str, bands: list[int] | None = None,
                                  max_dim: int = 512) -> tuple[np.ndarray, object] | None:
    """One real download+decode of the overview COG (see read_overview_array)."""
    from app.config import get_settings

    settings = get_settings()
    bucket = settings.r2_bucket_name
    key = cog_overview_key(water_source_id)
    client = None
    etag: str | None = None
    size: int | None = None
    # HEAD first: it tells us whether the copy already on disk IS this object, so
    # a warm container answers from /tmp instead of moving 8 MB over the wire.
    try:
        client = get_s3_client()
        head = client.head_object(Bucket=bucket, Key=key)
        etag = (head.get("ETag") or "").strip('"') or None
        size = head.get("ContentLength")
    except Exception:  # noqa: BLE001  (keep going: R2 blip, or no object at all)
        client = client or None
    data = _overview_disk_read(water_source_id, etag, size)
    if data is None:
        if client is None:
            return None
        try:
            obj = client.get_object(Bucket=bucket, Key=key)
            data = obj["Body"].read()
        except Exception:  # noqa: BLE001
            return None
        if etag:
            _overview_disk_write(water_source_id, etag, data)
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


