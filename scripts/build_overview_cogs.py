"""
Build 8x block-averaged overview COGs from the locally-cached merged COGs and
upload them to R2 (key: cogs/<water_source_id>/indices_ov8.tif).

Why: the advisory read path must serve zone means on Render's free tier, but a
decimated read of the full ~500MB COG (which has NO internal overviews) forces
GDAL to decode the whole raster — too slow / OOM for a small instance. An 8x
block-averaged overview is ~12MB, and zone means over a ring are statistically
unchanged by 8x averaging (each overview pixel is the mean of 64 source pixels).

Usage:
  python scripts/build_overview_cogs.py [--force]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings  # noqa: E402
from app.services.storage import cog_overview_key, get_s3_client, upload_file_to_key  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build_overview_cogs")

FACTOR = 8


def _georef_from_merged(path: Path) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    """Return (pixel_scale, tiepoint) read from the merged COG's tags."""
    with tifffile.TiffFile(path) as tif:
        page = tif.pages[0]
        if 33550 in page.tags:
            scale = tuple(page.tags[33550].value)  # (px, py, 0)
            tiepoint = tuple(page.tags[33922].value)  # (0,0,0,tx,ty,0)
        else:
            m = page.tags[34264].value  # ModelTransformationTag (4x4 flat)
            tx, a = m[3], m[0]
            ty, e = m[7], m[5]
            scale = (a, abs(e), 0.0)
            tiepoint = (0.0, 0.0, 0.0, tx, ty, 0.0)
    return scale, tiepoint


def build_overview(merged_path: Path, out_path: Path) -> None:
    """Write an 8x block-averaged overview COG for the merged COG at merged_path."""
    arr = tifffile.imread(str(merged_path))  # (h, w, bands) float32
    if arr.ndim != 3:
        raise ValueError(f"expected 3D (h,w,bands) array, got shape {arr.shape}")
    h, w, bands = arr.shape

    arr = np.where(np.isfinite(arr), arr, np.nan)

    oh = int(np.ceil(h / FACTOR))
    ow = int(np.ceil(w / FACTOR))
    ph, pw = oh * FACTOR, ow * FACTOR
    padded = np.full((ph, pw, bands), np.nan, dtype=np.float32)
    padded[:h, :w] = arr
    del arr

    blocks = padded.reshape(oh, FACTOR, ow, FACTOR, bands)
    sums = np.nansum(blocks, axis=(1, 3))
    counts = np.sum(np.isfinite(blocks), axis=(1, 3))
    del padded, blocks
    with np.errstate(invalid="ignore"):
        overview = sums / np.where(counts > 0, counts, np.nan)
    overview = overview.astype(np.float32)

    scale, tiepoint = _georef_from_merged(merged_path)
    px, py = scale[0] * FACTOR, scale[1] * FACTOR
    geo_key_directory = (1, 1, 0, 3, 1024, 0, 1, 2, 1025, 0, 1, 1, 2048, 0, 1, 4326)
    extratags = [
        (33550, "d", 3, (px, py, 0.0), True),
        (33922, "d", 6, list(tiepoint), True),
        (34735, "H", 16, list(geo_key_directory), True),
    ]

    tifffile.imwrite(
        str(out_path),
        overview,
        photometric="minisblack",
        planarconfig="contig",
        tile=(256, 256),
        compression="deflate",
        bigtiff=True,
        metadata=None,
        extratags=extratags,
    )


def overview_exists_in_r2(water_source_id: str) -> bool:
    settings = get_settings()
    client = get_s3_client()
    try:
        client.head_object(Bucket=settings.r2_bucket_name, Key=cog_overview_key(water_source_id))
        return True
    except Exception:  # noqa: BLE001
        return False


def run(force: bool = False) -> None:
    settings = get_settings()
    merged_files = sorted(Path("data/tiles").glob("*/merged.tif"))
    log.info(f"Found {len(merged_files)} cached merged COGs")
    ok, skipped, failed = 0, 0, 0
    for merged_path in merged_files:
        water_source_id = merged_path.parent.name
        if not force and overview_exists_in_r2(water_source_id):
            log.info(f"Skipping {water_source_id} (overview already in R2)")
            skipped += 1
            continue
        out_path = merged_path.parent / f"overview_{FACTOR}x.tif"
        try:
            log.info(f"Building 8x overview for {water_source_id} ...")
            build_overview(merged_path, out_path)
            log.info(f"  overview {out_path.stat().st_size / 1e6:.1f} MB")
            upload_file_to_key(out_path, cog_overview_key(water_source_id))
            log.info(f"  uploaded cogs/{water_source_id}/indices_ov8.tif")
            ok += 1
        except Exception as e:  # noqa: BLE001
            log.error(f"Failed for {water_source_id}: {e}")
            failed += 1

    log.info(f"Done. {ok} built+uploaded, {skipped} skipped, {failed} failed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Rebuild and re-upload every overview.")
    args = parser.parse_args()
    run(force=args.force)


if __name__ == "__main__":
    main()
