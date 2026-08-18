"""Quick end-to-end test of the tile-download + rasterio-merge pipeline."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ee
import requests
import rasterio
from rasterio.merge import merge

from app.services.gee_auth import init_earth_engine

ASSET = "projects/protean-tooling-466007-r0/assets/piosphere/7daacab2-dc66-4354-86eb-cabe6ad3a437"
SCALE = 1000
GRID = 2


def main():
    init_earth_engine()
    img = ee.Image(ASSET)
    coords = img.geometry().bounds().coordinates().getInfo()
    xs = [c[0] for c in coords[0]]
    ys = [c[1] for c in coords[0]]
    b = (min(xs), min(ys), max(xs), max(ys))
    print("bounds", b, flush=True)

    tmp = Path(tempfile.mkdtemp())
    paths = []
    for i in range(GRID):
        for j in range(GRID):
            x0 = b[0] + i * (b[2] - b[0]) / GRID
            x1 = b[0] + (i + 1) * (b[2] - b[0]) / GRID
            y0 = b[1] + j * (b[3] - b[1]) / GRID
            y1 = b[1] + (j + 1) * (b[3] - b[1]) / GRID
            region = {
                "type": "Polygon",
                "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
            }
            dl = ee.data.getDownloadId({
                "image": img,
                "region": region,
                "scale": SCALE,
                "crs": "EPSG:4326",
                "format": "GEO_TIFF",
            })
            url = ee.data.makeDownloadUrl(dl)
            r = requests.get(url, timeout=600)
            r.raise_for_status()
            p = tmp / f"t{i}{j}.tif"
            p.write_bytes(r.content)
            paths.append(p)
            print(f"tile {i}{j} size {len(r.content)}", flush=True)

    print("downloaded", len(paths), flush=True)
    srcs = [rasterio.open(str(p)) for p in paths]
    print("opened", flush=True)
    merged, transform = merge(srcs)
    print("merged shape", merged.shape, "dtype", merged.dtype, flush=True)

    out = tmp / "merged.tif"
    prof = srcs[0].profile.copy()
    prof.update(
        driver="GTiff",
        height=merged.shape[1],
        width=merged.shape[2],
        transform=transform,
        count=merged.shape[0],
        dtype=merged.dtype,
        compress="deflate",
        tiled=True,
        blockxsize=256,
        blockysize=256,
        bigtiff=True,
    )
    with rasterio.open(str(out), "w", **prof) as dst:
        dst.write(merged)
    print("merged size", out.stat().st_size, flush=True)
    for s in srcs:
        s.close()
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
