"""Predictor tests: the numbers that must LEAD the usable% (migration 016).

A weekly usable% is a REAR-VIEW number: by the time the ring reads 12% the herd
has already been eating it for a week. The predictors in map_renderer read the
same overview bytes and answer the leading questions instead:

  * how much of the near-water ring is worn out compared to the outer ring
    (grazing pressure without a single head count),
  * how broken up the remaining forage is (a share can hold one block or 200
    specks, and only one of them feeds a herd),
  * what the moisture / season indices say (NDMI curing, VCI poor-for-season).

These tests pin the arithmetic on synthetic scenes, and - just as important -
that an unreadable scene yields NOTHING rather than a zero that would later look
like a measurement.
"""
from __future__ import annotations

import math
import sys
import types

import numpy as np

sys.path.insert(0, ".")

from app.services import map_renderer as m  # noqa: E402

LON_C, LAT_C = 37.0, 0.5
RADIUS_KM = 5.0
RES = 0.0005  # ~55 m per pixel
SIDE = 240


def _transform():
    """A north-up transform whose grid is centred on (LON_C, LAT_C)."""
    half = SIDE / 2.0
    return types.SimpleNamespace(
        a=RES, e=-RES,
        c=LON_C - half * RES,
        f=LAT_C + half * RES,
    )


def _km_grid() -> np.ndarray:
    """Distance from the water point, in km, on the same grid the masks use."""
    tf = _transform()
    cols = tf.c + np.arange(SIDE, dtype=np.float32) * tf.a
    rows = tf.f + np.arange(SIDE, dtype=np.float32) * tf.e
    km_x = 111.32 * math.cos(math.radians(LAT_C))
    dlon = cols[None, :] - LON_C
    dlat = rows[:, None] - LAT_C
    return np.sqrt((dlon * km_x) ** 2 + (dlat * 110.57) ** 2)


def _stack(ndvi, satvi, bsi, ndmi=None, vci=None) -> np.ndarray:
    bands = [ndvi, satvi, bsi]
    if ndmi is not None:
        bands.append(ndmi)
        bands.append(vci if vci is not None else ndmi)
    return np.stack(bands).astype(np.float32)


def _worn_near_good_far() -> np.ndarray:
    """The classic piosphere: grazed out at the water, still good at the edge.

    Near the point (inside NEAR_FRAC of the ring) almost everything is bare; the
    outer ring is standing dry forage. The plain usable% cannot tell this apart
    from "one uniform 50% ring", and the two need opposite advice.
    """
    km = _km_grid()
    near = km <= RADIUS_KM * m.NEAR_FRAC
    ndvi = np.full(km.shape, 0.10, dtype=np.float32)  # no active growth anywhere
    satvi = np.where(near, 0.02, 0.30).astype(np.float32)  # bare near, dry far
    bsi = np.where(near, 0.10, 0.05).astype(np.float32)
    ndmi = np.full(km.shape, -0.05, dtype=np.float32)
    vci = np.full(km.shape, 28.0, dtype=np.float32)
    return _stack(ndvi, satvi, bsi, ndmi, vci)


def test_near_far_contrast_and_indices() -> None:
    arr = _worn_near_good_far()
    pred = m._ring_predictors(arr, _transform(), LON_C, LAT_C, RADIUS_KM)
    assert "near_pct" in pred and "far_pct" in pred and "gap_pp" in pred, pred
    assert pred["near_pct"] == 0, pred
    assert pred["far_pct"] >= 95, pred
    assert pred["gap_pp"] == pred["far_pct"] - pred["near_pct"], pred
    assert pred["gap_pp"] > 0, "grazed-at-the-water ring must read positive gap"
    idx = pred["indices"]
    assert set(idx) == {"ndvi", "satvi", "bsi", "ndmi", "vci"}, idx
    assert abs(idx["ndmi"] + 0.05) < 1e-3, idx
    assert abs(idx["vci"] - 28.0) < 1e-3, idx
    assert "patch_pct" in pred and "patch_count" in pred, pred
    assert 0.0 <= pred["patch_pct"] <= 100.0, pred
    print("near/far contrast OK (gap %d pp, patch %.1f%% in %d)"
          % (pred["gap_pp"], pred["patch_pct"], pred["patch_count"]))


def test_uniform_ring_reads_as_no_pressure() -> None:
    """Same share all over: no grazing signature, and the gap says so."""
    shape = _km_grid().shape
    arr = _stack(np.full(shape, 0.10, dtype=np.float32),
                 np.full(shape, 0.30, dtype=np.float32),
                 np.full(shape, 0.05, dtype=np.float32),
                 np.full(shape, 0.02, dtype=np.float32),
                 np.full(shape, 48.0, dtype=np.float32))
    pred = m._ring_predictors(arr, _transform(), LON_C, LAT_C, RADIUS_KM)
    assert pred["near_pct"] == pred["far_pct"] == 100, pred
    assert pred["gap_pp"] == 0, pred
    print("uniform ring OK (gap 0 pp)")


def test_fragmentation_is_its_own_number() -> None:
    """One block of grass and many specks can cover similar ground and are not the
    same pasture. The share cannot tell them apart; the patch numbers must."""
    km = _km_grid()
    ring = km <= RADIUS_KM
    block = km <= 3.0
    specks = np.zeros_like(ring)
    for r in range(20, SIDE - 20, 10):
        for c in range(20, SIDE - 20, 10):
            specks[r:r + 4, c:c + 4] = True
    specks &= ring

    big = m._patch_stats(block & ring, ring)
    small = m._patch_stats(specks, ring)
    assert big is not None and small is not None, (big, small)
    assert big[1] == 1, "one block must count as one patch: %r" % (big,)
    assert small[1] >= 10, "specks must count as many patches: %r" % (small,)
    assert big[0] > 25, "a 3 km block is a large share of a 5 km ring: %r" % (big,)
    assert small[0] < 3, "scattered specks are a tiny patch: %r" % (small,)
    print("fragmentation OK (block %.1f%% in %d, specks %.1f%% in %d)"
          % (big[0], big[1], small[0], small[1]))


def test_unreadable_scene_stores_nothing() -> None:
    """Cloud over the whole ring: no percentages, no indices, no patch numbers -
    and no exception either. A fabricated zero here would be quoted back as a
    real measurement next week."""
    shape = _km_grid().shape
    nan = np.full(shape, np.nan, dtype=np.float32)
    pred = m._ring_predictors(_stack(nan, nan, nan, nan, nan),
                              _transform(), LON_C, LAT_C, RADIUS_KM)
    assert "near_pct" not in pred and "indices" not in pred, pred
    assert "patch_pct" not in pred, pred
    # ... and an empty / too-thin array must not raise at all.
    assert m._ring_predictors(None, _transform(), LON_C, LAT_C, RADIUS_KM) == {}
    assert m._ring_predictors(np.zeros((2, 4, 4), np.float32), _transform(),
                              LON_C, LAT_C, RADIUS_KM) == {}
    assert m._zone_usable_pct(np.zeros((4, 4), np.uint8),
                              np.ones((4, 4), bool)) is None
    print("unreadable scene OK (nothing stored, nothing raised)")


def test_three_band_stack_keeps_the_old_answer() -> None:
    """An overview built before NDMI/VCI existed still classifies and still gives
    near/far + patches; it just has no moisture index to offer."""
    shape = _km_grid().shape
    arr = _stack(np.full(shape, 0.10, dtype=np.float32),
                 np.full(shape, 0.30, dtype=np.float32),
                 np.full(shape, 0.05, dtype=np.float32))
    pred = m._ring_predictors(arr, _transform(), LON_C, LAT_C, RADIUS_KM)
    assert set(pred["indices"]) == {"ndvi", "satvi", "bsi"}, pred
    assert pred["near_pct"] == 100, pred
    print("3-band stack OK (moisture index simply absent)")


def test_near_frac_is_inside_the_ring() -> None:
    """NEAR_FRAC must stay a fraction: at 1.0 the near zone IS the ring and the
    gap would be structurally zero, hiding the grazing signature entirely."""
    assert 0.0 < m.NEAR_FRAC < 0.5, m.NEAR_FRAC
    print("NEAR_FRAC OK (%.2f of the ring)" % m.NEAR_FRAC)


def test_classes_can_be_reused() -> None:
    """The status call classifies the array it already holds; the predictors must
    accept that classification instead of walking the same pixels a second time -
    and produce exactly the same numbers either way."""
    arr = _worn_near_good_far()
    own = m._ring_predictors(arr, _transform(), LON_C, LAT_C, RADIUS_KM)
    reused = m._ring_predictors(arr, _transform(), LON_C, LAT_C, RADIUS_KM,
                                m._classify(arr))
    assert own == reused, (own, reused)
    print("one classification, same numbers OK")


if __name__ == "__main__":
    test_near_far_contrast_and_indices()
    test_uniform_ring_reads_as_no_pressure()
    test_fragmentation_is_its_own_number()
    test_unreadable_scene_stores_nothing()
    test_three_band_stack_keeps_the_old_answer()
    test_near_frac_is_inside_the_ring()
    test_classes_can_be_reused()
    print("\npasture predictor tests OK.")


