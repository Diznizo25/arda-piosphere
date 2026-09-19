# ADR 005 — "Is there water now" and "is this usually wet" are different questions

**Status:** accepted · **Phase:** 2 · **Date:** 2026-09-19

## Context

`classify_water_reliability` reads JRC Global Surface Water **monthly
recurrence** — a multi-decade average of how often a pixel held water in this
calendar month. It is a climatology. It answers *"is this usually wet in
September"* and can say nothing whatever about today.

But it was the only satellite input to the water half of the advisory, and it
was worded in the present tense: *"maji ya kutegemewa kipindi hiki"* — reliable
water for now. A pan bone dry for three months still produced that sentence,
because September is usually wet there.

The team already understood the limitation — migration 009 exists precisely
because the satellite cannot see a broken pump, and herder reports gate
guidance. But that left the satellite contributing **nothing about the present**
while being phrased as though it did.

Meanwhile **NDWI was already computed** (`gee_indices.py:83`), exported,
transferred and stored in every ~500 MB COG since the beginning — and read by
nothing. NDWI is the present-tense observation the system lacked.

## Decision

Add `WaterPresence` as a **separate** signal, reported alongside reliability,
never replacing it:

| | question | source |
| --- | --- | --- |
| `WaterReliability` | is this usually wet this month? | JRC GSW recurrence (climatology) |
| `WaterPresence` | was water seen here last overpass? | NDWI (observation) |

Read at the **point**, not the ring, and as a **high percentile**, not a mean
(`raster_read.read_water_signal`). Two reasons, both practical:

- A 25 km ring mean of NDWI is dominated by dry land and would never cross any
  threshold, whatever the water did.
- A borehole trough or a shrinking pan is far smaller than one 80 m overview
  pixel, so the signal lives in the wettest few pixels of a small neighbourhood.
  A mean over 300 m buries it.

### Uncertainty is asymmetric, so the middle band stays silent

Wrongly saying *no water* sends a herd on a longer trek than necessary.
Wrongly saying *water* sends them to a dry hole. So NDWI between
`ndwi_no_water_threshold` (0.00) and `ndwi_open_water_threshold` (0.20) returns
`UNCERTAIN`, and `UNCERTAIN`/`UNKNOWN` produce **no line at all** rather than
hedging out loud. Fewer than `MIN_WATER_SAMPLE_PX` valid pixels is `UNKNOWN`.

### Every presence line carries its date

It is one observation from one overpass, not a live status. *"Satelaiti
haikuona maji hapa tarehe 14/09"* — the satellite saw no water here on 14/09.
Without the date it reads as a guarantee.

### The herder still outranks it

Unchanged: `water_status.py` and the guidance gate in `water_reach.py` remain
the authority on whether a point is usable. NDWI cannot see a broken pump, a
silted trough, or a locked borehole.

## On NDRE — keep computing, decide at Phase 5

NDRE is the other band that is computed, stored and never read. It is tempting
to delete it, and together with the now-used NDWI it is a quarter of every COG.

We are **not** removing it yet. Band positions are implicit in the COG layout
(`raster_read.BAND_NAMES`), so dropping a band shifts every index after it and
invalidates every COG already built. That churn is not worth 12.5% of storage
today.

NDRE also has a plausible job nobody has tested: red-edge responds to chlorophyll
and nitrogen, which is a forage **quality** signal rather than quantity — "is
this grass worth the walk", not just "is there grass". That is a research
question (R1/R3), not a deletion.

Phase 5 rewrites the raster layout for tile-grid compute anyway. Decide there,
with a measured answer about quality in hand.

## Consequences

- `AdvisoryResult` gains `water_presence`. Existing `water_reliability`
  semantics are untouched.
- `ndwi_open_water_threshold: 0.20` is the conventional McFeeters cutoff, but
  ASAL water is turbid and pans are small. It is a **starting default**, to be
  tuned against herder-reported status — which the system already collects, and
  which `zone_index_history` now preserves (R0/R1).
- Expect disagreements between GSW, NDWI and herder reports. Those are the
  interesting rows, not errors: they are where the calibration data is.

## What would change our mind

If NDWI at 80 m proves too coarse to see the water points that actually matter
here — most of them boreholes and troughs rather than open pans — the answer is
Sentinel-1 SAR (cloud-penetrating, 6–12 day revisit), not a looser threshold.
