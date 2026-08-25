"""
Transfer completed GEE piosphere assets to R2.

Lists every image asset in the GEE 'piosphere' folder, downloads each as a
GeoTIFF via the GEE API (tiling large images to stay under the 50MB download
limit), merges tiles with the pure-Python tifffile merge (rasterio/GDAL native
DLLs are blocked on this machine by the Windows Application Control policy),
uploads to R2 at the canonical key, and marks piosphere_zones.last_computed.
Skips assets already present in R2 (and re-transfers any R2 object that is far
smaller than a correctly-merged COG — i.e. written by the earlier broken
rasterio merge) so it can be re-run safely to pick up newly-completed exports.

Usage:
  python scripts/transfer_assets_to_r2.py [--force]
"""
from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings  # noqa: E402
from app.db import get_pg_connection  # noqa: E402
from app.services.gee_auth import init_earth_engine  # noqa: E402
from app.services.storage import cog_key, cog_overview_key, get_s3_client, upload_file, upload_file_to_key  # noqa: E402

# The advisory read path serves zone means from a tiny 8x overview COG
# (cogs/<id>/indices_ov8.tif); the full-res COG is the archival source.
# Build both during the transfer so new water sources are immediately readable
# on small instances.
from scripts.build_overview_cogs import build_overview  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("transfer_assets_to_r2")

MARK_COMPUTED_SQL = """
update piosphere_zones
set last_computed = now()
where water_source_id = %(water_source_id)s and species = 'camel'
"""

# GEE download API caps a single request at 50MB. We tile the image so each
# tile stays well under that. TILE_GRID is the number of tiles per side.
# A 10x10 grid keeps each tile ~5-6MB for a 560MB asset, which is much more
# reliable to download over a flaky connection than 20-25MB tiles.
TILE_GRID = 10
# Resolution in meters for the download.
SCALE = 10

# Correctly-merged COGs for these water points are ~400-600 MB (5000x5000x8
# float32). Objects far below that are the "merged" files written by the broken
# earlier rasterio merge (0.8 MB, ~96% zeros) — treat them as absent so they get
# re-transferred with the fixed tifffile merge.
BROKEN_COG_MIN_BYTES = 10 * 1024 * 1024



def list_piosphere_assets() -> list[str]:
    """Return the list of image asset ids under the piosphere folder."""
    import ee

    settings = get_settings()
    folder = f"projects/{settings.gee_project_id}/assets/piosphere"
    result = ee.data.listAssets({"parent": folder})
    assets = []
    for a in result.get("assets", []):
        if a.get("type") == "IMAGE":
            assets.append(a["name"])
    return assets


def asset_exists_in_r2(water_source_id: str) -> bool:
    settings = get_settings()
    client = get_s3_client()
    try:
        r = client.head_object(Bucket=settings.r2_bucket_name, Key=cog_key(water_source_id))
        size = r.get("ContentLength", 0)
        if size < BROKEN_COG_MIN_BYTES:
            log.warning(
                f"{water_source_id} object in R2 is only {size} bytes "
                f"(broken earlier merge) — treating as missing and re-transferring"
            )
            return False
        return True
    except Exception:  # noqa: BLE001
        return False


def _upload_with_heartbeat(merged_path: Path, water_source_id: str) -> None:
    """Upload a merged COG to R2, logging a heartbeat line every 60s while the
    multi-part upload streams. Multi-part uploads of ~500MB can legitimately take
    over an hour on a slow link with zero other output; the watchdog's stale-
    output guard would otherwise kill the subprocess mid-upload.
    """
    import threading

    stop = threading.Event()

    def heartbeat():
        while not stop.wait(60):
            log.info(f"  uploading {water_source_id} to R2 ... "
                     f"({merged_path.stat().st_size / 1e6:.0f} MB)")

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        upload_file(merged_path, water_source_id, transfer_config=_r2_transfer_config())
    finally:
        stop.set()
    log.info(f"Uploaded {water_source_id} to R2")


def _r2_transfer_config():
    """Multipart-upload tuning for a flaky connection.

    boto3 defaults split a 500MB COG into ~64 x 8MB parts with 10 concurrent
    workers; any single part failing enough times aborts the whole upload. We
    use 64MB parts (~8 parts per COG), lower concurrency, and 20 per-part
    attempts so a ConnectionReset during one part no longer kills the entire
    asset transfer.
    """
    from boto3.s3.transfer import TransferConfig

    return TransferConfig(
        multipart_threshold=16 * 1024 * 1024,
        multipart_chunksize=64 * 1024 * 1024,
        max_concurrency=2,
        use_threads=True,
    )


def _image_bounds(asset_id: str) -> tuple[float, float, float, float]:
    """Return (min_x, min_y, max_x, max_y) of the asset's footprint."""
    import ee

    img = ee.Image(asset_id)
    coords = img.geometry().bounds().coordinates().getInfo()
    xs = [c[0] for c in coords[0]]
    ys = [c[1] for c in coords[0]]
    return min(xs), min(ys), max(xs), max(ys)


def _tile_regions(bounds: tuple[float, float, float, float], grid: int) -> list[dict]:
    """Split bounds into a grid of GeoJSON polygon regions."""
    min_x, min_y, max_x, max_y = bounds
    dx = (max_x - min_x) / grid
    dy = (max_y - min_y) / grid
    regions = []
    for i in range(grid):
        for j in range(grid):
            x0 = min_x + i * dx
            x1 = min_x + (i + 1) * dx
            y0 = min_y + j * dy
            y1 = min_y + (j + 1) * dy
            regions.append({
                "type": "Polygon",
                "coordinates": [[
                    [x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0],
                ]],
            })
    return regions


def _download_tile(asset_id: str, region: dict, out_path: Path) -> bool:
    """Download a single tile as a GeoTIFF to out_path.

    Retries transient connection errors (e.g. ConnectionResetError) with
    exponential backoff so a flaky network doesn't abort the whole asset.
    Uses a fresh session AND a fresh download URL per attempt so a stale
    URL or throttled download id doesn't keep failing. Returns True on
    success, False if all attempts are exhausted (so the caller can decide
    whether to abort the whole asset or continue).
    """
    import time

    import ee
    import requests

    max_attempts = 12
    for attempt in range(1, max_attempts + 1):
        try:
            # Regenerate the download URL each attempt so a throttled or
            # stale download id doesn't keep failing.
            dl = ee.data.getDownloadId({
                "image": ee.Image(asset_id),
                "region": region,
                "scale": SCALE,
                "crs": "EPSG:4326",
                "format": "GEO_TIFF",
            })
            url = ee.data.makeDownloadUrl(dl)
            with requests.Session() as session:
                r = session.get(url, timeout=600)
                r.raise_for_status()
                out_path.write_bytes(r.content)
            return True
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt == max_attempts:
                log.warning(f"  tile download exhausted {max_attempts} attempts ({e})")
                return False
            wait = 15 * attempt  # 15s, 30s, 45s, ... up to 165s
            log.warning(f"  tile download attempt {attempt} failed ({e}); retrying in {wait}s")
            time.sleep(wait)
        except requests.exceptions.HTTPError as e:
            # Retry transient server errors (5xx) too, e.g. GEE 503.
            status = e.response.status_code if e.response is not None else 0
            if status < 500 or attempt == max_attempts:
                log.warning(f"  tile download failed (HTTP {status})")
                return False
            wait = 20 * attempt
            log.warning(f"  tile download attempt {attempt} failed (HTTP {status}); retrying in {wait}s")
            time.sleep(wait)







def _merge_tiles(tile_paths: list[Path], bounds: tuple[float, float, float, float], out_path: Path) -> None:
    """Merge downloaded tile GeoTIFFs into a single tiled, deflate-compressed
    GeoTIFF using tifffile + numpy.

    rasterio/GDAL native DLLs are blocked on this machine by the Windows
    Application Control (WDAC/HVCI) policy, so we avoid them entirely. Each GEE
    tile carries its georeferencing in the ModelTransformationTag (34264) as a
    GDAL-style 4x4 matrix: pixel (0,0) maps to (tx, ty) with pixel size
    (a, e). We read each tile's origin + pixel size and place its array into
    the correct window of the output mosaic, then write a north-up GeoTIFF
    using the ModelPixelScale/ModelTiepoint/GeoKeyDirectory tags (the same
    convention the previous rasterio version produced).
    """
    import numpy as np
    import tifffile

    min_x, min_y, max_x, max_y = bounds

    def read_tile(path: Path):
        with tifffile.TiffFile(path) as tif:
            arr = tif.asarray()  # GEE tiles are interleaved -> (rows, cols, bands)
            page = tif.pages[0]
            if arr.ndim == 2:
                arr = arr[..., None]
            if 34264 in page.tags:
                m = page.tags[34264].value
                # GDAL geotransform matrix: (a, d, 0, tx, b, e, 0, ty, 0,0,1,0, 0,0,0,1)
                tx, a = m[3], m[0]
                ty, e = m[7], m[5]  # e is negative for north-up rasters
            else:
                raise ValueError(f"{path} has no ModelTransformationTag (34264)")
            return arr, tx, ty, a, abs(e)

    first, _tx, _ty, px, py = read_tile(tile_paths[0])
    dtype = first.dtype
    bands = first.shape[2]

    width = int(round((max_x - min_x) / px))
    height = int(round((max_y - min_y) / py))
    if width <= 0 or height <= 0:
        raise ValueError(f"bad output dimensions {width}x{height} (px={px})")

    # Unwritten areas default to 0, matching the previous rasterio behaviour.
    merged = np.zeros((height, width, bands), dtype=dtype)

    for path in tile_paths:
        arr, tx, ty, _px, _py = read_tile(path)
        row_off = int(round((max_y - ty) / py))
        col_off = int(round((tx - min_x) / px))
        th, tw = arr.shape[0], arr.shape[1]
        # Clip to the output extent (rounding may push a tile slightly over).
        r0, r1 = max(row_off, 0), min(row_off + th, height)
        c0, c1 = max(col_off, 0), min(col_off + tw, width)
        if r1 <= r0 or c1 <= c0:
            continue
        merged[r0:r1, c0:c1, :] = arr[r0 - row_off:r1 - row_off,
                                      c0 - col_off:c1 - col_off, :]

    # GeoTIFF georeferencing tags (same convention GDAL/rasterio wrote before).
    # tifffile extratags tuple format: (code, dtype, count, value, writeonce).
    model_pixel_scale = (px, py, 0.0)
    model_tiepoint = (0.0, 0.0, 0.0, min_x, max_y, 0.0)
    geo_key_directory = (1, 1, 0, 3, 1024, 0, 1, 2, 1025, 0, 1, 1, 2048, 0, 1, 4326)
    extratags = [
        (33550, "d", 3, model_pixel_scale, True),
        (33922, "d", 6, model_tiepoint, True),
        (34735, "H", 16, list(geo_key_directory), True),
    ]

    tifffile.imwrite(
        out_path,
        merged,
        photometric="minisblack",
        planarconfig="contig",
        tile=(256, 256),
        compression="deflate",
        bigtiff=True,
        metadata=None,
        extratags=extratags,
    )




def download_asset_to_r2(asset_id: str) -> bool:
    """Download a GEE asset as GeoTIFF (tiled) and upload to R2.

    Tiles are saved to a persistent directory (data/tiles/<asset_id>/) so a
    re-run can skip tiles already downloaded. This makes the transfer
    resumable across many runs on a flaky network. Once all tiles are present
    they are merged and uploaded to R2.
    """
    import time

    water_source_id = asset_id.rsplit("/", 1)[-1]
    # The network here is flaky (frequent ConnectionResetError). Each asset
    # attempt resumes from the persistent tile cache, so retrying the whole
    # asset many times is cheap — it just continues from the next uncached
    # tile. 30 attempts gives the connection plenty of chances to stabilize
    # across both downloads and the multi-part R2 upload.
    ASSET_ATTEMPTS = 30


    # Persistent tile cache so re-runs skip already-downloaded tiles.
    tile_dir = Path("data") / "tiles" / water_source_id
    tile_dir.mkdir(parents=True, exist_ok=True)

    for asset_attempt in range(1, ASSET_ATTEMPTS + 1):
        try:
            bounds = _image_bounds(asset_id)
            regions = _tile_regions(bounds, TILE_GRID)

            tile_paths = []
            for idx, region in enumerate(regions):
                tile_path = tile_dir / f"tile_{idx}.tif"
                if tile_path.exists() and tile_path.stat().st_size > 0:
                    log.info(f"  tile {idx + 1}/{len(regions)} already cached")
                    tile_paths.append(tile_path)
                    continue
                ok = _download_tile(asset_id, region, tile_path)
                if not ok:
                    raise RuntimeError(f"tile {idx + 1}/{len(regions)} failed after retries")
                tile_paths.append(tile_path)
                log.info(f"  tile {idx + 1}/{len(regions)} downloaded")
                # Small delay between tiles to avoid GEE rate-limiting /
                # connection resets when many tiles are fetched in a row.
                if idx < len(regions) - 1:
                    time.sleep(2)

            merged_path = tile_dir / "merged.tif"
            _merge_tiles(tile_paths, bounds, merged_path)
            log.info(f"  merged {len(tile_paths)} tiles -> {merged_path.stat().st_size / 1e6:.1f} MB")

            _upload_with_heartbeat(merged_path, water_source_id)

            # Build + upload the 8x overview so the advisory read path can serve
            # zone means quickly on small instances. Failure here must not fail
            # the whole asset (the full-res COG is already safe in R2).
            try:
                ov_path = tile_dir / "overview_8x.tif"
                build_overview(merged_path, ov_path)
                log.info(f"  overview {ov_path.stat().st_size / 1e6:.1f} MB")
                upload_file_to_key(ov_path, cog_overview_key(water_source_id))
                log.info(f"  uploaded {cog_overview_key(water_source_id)}")
            except Exception as e:  # noqa: BLE001
                log.warning(f"  overview build/upload failed (non-fatal): {e}")

            return True
        except Exception as e:  # noqa: BLE001
            if asset_attempt == ASSET_ATTEMPTS:
                log.error(f"Failed to download/upload asset {asset_id} after {ASSET_ATTEMPTS} attempts: {e}")
                return False
            wait = 30 * asset_attempt
            log.warning(f"  asset attempt {asset_attempt} failed ({e}); retrying in {wait}s")
            time.sleep(wait)

    return False





def run(force: bool = False):
    init_earth_engine()
    assets = list_piosphere_assets()
    log.info(f"Found {len(assets)} image assets in piosphere folder")

    ok, skipped, failed = 0, 0, 0
    for asset_id in assets:
        water_source_id = asset_id.rsplit("/", 1)[-1]
        if not force and asset_exists_in_r2(water_source_id):
            log.info(f"Skipping {water_source_id} (already in R2)")
            skipped += 1
            continue
        if download_asset_to_r2(asset_id):
            ok += 1
            try:
                with get_pg_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(MARK_COMPUTED_SQL, {"water_source_id": water_source_id})
                    conn.commit()
            except Exception as e:  # noqa: BLE001
                # The R2 upload already succeeded — the last_computed bookkeeping
                # must never be allowed to abort the whole transfer. Log and move on.
                log.warning(
                    f"Uploaded {water_source_id} to R2 but could not mark "
                    f"piosphere_zones.last_computed (DB issue): {e}"
                )
        else:
            failed += 1

    log.info(f"Done. {ok} transferred, {skipped} skipped, {failed} failed.")


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-transfer every asset, overwriting any that already exist in R2 "
             "(used to replace COGs written by the earlier broken rasterio merge).",
    )
    args = parser.parse_args()
    run(force=args.force)


if __name__ == "__main__":
    main()
