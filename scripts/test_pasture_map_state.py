"""Pasture-on-the-map state tests: a point that is DONE must never look empty.

On 18 Oct a freshly built point was signed off ("done") while /mapview showed no
pasture at all: the page waits on nothing, the satellite read is started in the
background, and the numbers arrive on a later request. These tests pin the three
things that make that temporary state honest and self-healing:
  * the page says the satellite is still being read (not a silent blank panel)
  * the legend can refill itself without a page reload
  * the image request joins the page's download instead of starting a second one
  * "done" is gated on the 8x overview the map actually paints from
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")

from app.routers.mapview import mapview_page  # noqa: E402
from app.services import map_renderer as m  # noqa: E402


def test_page_says_it_is_still_reading_instead_of_showing_nothing() -> None:
    """Cold cache: the server knows the layer exists but has no percentages yet.
    The page must still show the pasture panel, say what is happening, and be able
to fill the numbers in by itself."""
    real_status = m.pasture_overlay_status
    m.pasture_overlay_status = lambda *a, **k: {
        "bounds": [[0.50, 37.20], [0.62, 37.29]], "available": True,
        "usable_pct": None, "frac": None, "blank_pct": None,
    }
    try:
        for lang, phrase in (("swa", "tunasoma satelaiti"),
                             ("eng", "reading the satellite")):
            html = mapview_page(lat=0.3525, lon=37.583, species="camel", lang=lang,
                                id="88bc1e17-a5f6-4c4d-a410-e551e4af7e54",
                                numbered=None, name=None, interval="daily", focus=1)
            body = html.body.decode("utf-8", errors="replace")
            assert '"frac": null' in body, "the pending state must reach the page"
            assert phrase in body, lang + ": herder is not told the read is running"
            assert "function refreshPastureSummary" in body, "no self-refill"
            assert "function legendHtml" in body, "legend must be rebuildable"
            assert "pasture.png" in body, "the layer itself must still be requested"
            print(lang + " pending-pasture page OK")
    finally:
        m.pasture_overlay_status = real_status


def test_blocking_read_joins_the_inflight_download() -> None:
    """The page starts the 8 MB read off-thread; the pasture PNG the browser asks
    for next must WAIT for that read, not download the same bytes again."""
    from app.services import raster_read as rr

    real_dir = rr._OVERVIEW_DISK_DIR
    real_uncached = rr._read_overview_array_uncached
    calls = {"n": 0}

    def slow_uncached(wsid, bands=None, max_dim=512):
        calls["n"] += 1
        time.sleep(0.4)
        return np.ones((3, 4, 4)), None

    try:
        rr._OVERVIEW_DISK_DIR = Path(tempfile.mkdtemp()) / "cold-container"
        rr._read_overview_array_uncached = slow_uncached
        rr.clear_overview_cache()
        t0 = time.perf_counter()
        assert rr.read_overview_array_warm("join-point", bands=[1, 3, 4]) is None
        assert time.perf_counter() - t0 < 0.4, "the page waited for a cold download"
        got = rr.read_overview_array_blocking("join-point", bands=[1, 3, 4],
                                             timeout_s=5)
        assert got is not None and got[0].shape == (3, 4, 4), got
        assert calls["n"] == 1, str(calls["n"]) + " downloads for one page + its image"
    finally:
        rr._OVERVIEW_DISK_DIR = real_dir
        rr._read_overview_array_uncached = real_uncached
        rr.clear_overview_cache()
    print("blocking read OK (one download shared by page and image)")


def test_build_done_requires_the_overview_the_map_paints_from() -> None:
    """"done" used to mean only "the 500 MB COG is in R2", while the map paints
    from the 8x overview. A failed overview upload then left a point Done and
    colourless for a whole week. Both objects are the gate."""
    import app.services.storage as storage
    from scripts.build_water_point import _raster_missing_in_r2

    real_client = storage.get_s3_client
    seen: list[str] = []

    class NeverOverview:
        def head_object(self, Bucket=None, Key=None, **_kw):
            seen.append(Key)
            if Key and Key.endswith("_ov8.tif"):
                raise RuntimeError("404 NoSuchKey")
            return {"ContentLength": 1}

    class BothObjects:
        def head_object(self, Bucket=None, Key=None, **_kw):
            seen.append(Key)
            return {"ContentLength": 1}

    try:
        storage.get_s3_client = lambda *a, **k: NeverOverview()
        missing = _raster_missing_in_r2("no-overview")
        assert len(seen) == 2, "the gate must look at both objects"
        assert len(missing) == 1 and missing[0].endswith("_ov8.tif"), missing
        storage.get_s3_client = lambda *a, **k: BothObjects()
        assert _raster_missing_in_r2("ready") == []
    finally:
        storage.get_s3_client = real_client
    print("build gate OK (done requires COG + 8x overview)")


if __name__ == "__main__":
    test_page_says_it_is_still_reading_instead_of_showing_nothing()
    test_blocking_read_joins_the_inflight_download()
    test_build_done_requires_the_overview_the_map_paints_from()
    print("\npasture-on-map state tests OK.")
