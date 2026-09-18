# ADR 003 — Report a distribution, and stay silent when the sky was not clear

**Status:** accepted · **Phase:** 1 · **Date:** 2026-09-18

## Context

Three separate problems, one root cause: the advisory described a 25 km ring
(~1,960 km²) with a single number and had no way to know whether it had seen it.

**1. The mean describes nowhere.** `read_zone_stats` averaged every band across
the whole ring and handed one value per band to the classifier. Forage in ASAL
is patchy. A ring that is a 17% green riverine strip through 83% overgrazed bare
ground averages to `BARE_DEGRADED` — a label matching **no pixel in the ring** —
and the herder was told *"eneo tupu, malisho hafifu"* (bare ground, very little
pasture) about land holding a strip they could have walked to. Meanwhile a
uniformly marginal ring, where there genuinely is nowhere to go, returned
`UNCERTAIN`. Those two rings demand opposite advice and the mean cannot separate
them.

**2. Coverage measured geometry.** `ZoneStats.valid_pixel_count` was
`mask.sum()` — pixels inside the *polygon*, not pixels carrying data — so
`coverage_ratio` was polygon area over bounding-box area, which is π/4 for any
circle. Measured on a 60%-clouded raster at four radii it returned 0.717, 0.775,
0.765, 0.775: the same numbers it would return under a clear sky. The per-band
finite count *was* computed in the loop and then discarded. And no caller in
`app/` read `coverage_ratio` at all.

**3. The direction was computed and thrown away.**
`map_renderer.pasture_guidance()` already returns bearing and distance to the
nearest walkable good patch, for the map. The text never used it.

## Decision

- `ZoneStats` carries real per-band `valid_counts`, a `classified_count`, and
  `class_fractions`. `coverage_ratio` is now `classified / in_ring` — the share
  of the ring usable in **every** band the classifier needs. The honestly-named
  `in_ring_count` replaces `valid_pixel_count`.
- The advisory reports the **distribution** plus **bearing and distance** to the
  nearest patch, instead of one label.
- Below `MIN_COVERAGE_RATIO` (0.25) the advisory answers the water half and
  **declines to describe the forage**, with `no_forage_reason="low_coverage"`.

## Coarse bands, not percentages — for now

The message says *"Sehemu ndogo tu ya eneo hili ina malisho"* ("only a small
part of this area has grazing"), not "17%".

`config/advisory_thresholds.yaml` states its numbers are "starting defaults, not
validated against ground truth yet". A band degrades gracefully when a threshold
is slightly wrong; a percentage invites a precision the data cannot support.
Move to numbers once the thresholds are calibrated against real herder reports
(research note R1). The bearing and distance carry most of the value and do not
depend on the thresholds being exactly right.

## Guidance is capped at the herder's own reach

The nearest good patch is only offered when it is within `effective_radius_km`
for that species and watering interval. Pointing a cattle herder with a 7 km
ring at grazing 18.5 km away reads as guidance but is a two-day trek. Beyond
their reach the advisory stays silent rather than implying it is walkable.

## Silence is a feature

"We could not see clearly this week" is a better answer than a confident label
inferred from a quarter of a ring. This is the same principle already applied to
`WaterReliability.UNKNOWN`: never report a bad condition merely because data is
missing.

## Consequences

- More "we cannot say" replies during cloudy periods. This is the intended
  trade and should be watched: if the rate is high, the fix is a shorter
  compositing window (Phase 5), not a lower threshold.
- `MIN_COVERAGE_RATIO = 0.25` is itself a starting default. Tune it from the
  coverage distribution in `zone_index_history` after one season (R0).
- `AdvisoryResult` gains `class_fractions`, `coverage_ratio`, `observed_at` and
  `no_forage_reason`, so callers can distinguish "we looked and it is bare" from
  "we could not look" — which the old API could not express.
