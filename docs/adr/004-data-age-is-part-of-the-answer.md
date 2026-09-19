# ADR 004 — The observation date is part of the answer

**Status:** accepted · **Phase:** 1 · **Date:** 2026-09-18

## Context

`piosphere_zones.last_computed` was written by four pipeline scripts and read by
**nothing** in `app/`. A COG from six months ago was served with exactly the
same confident wording as one from yesterday, and the herder was never told the
date.

The product's entire claim is timeliness — *Know first. Act in time.* The
satellite refresh blends a 30-day window and runs on a ~14-day cycle, so a
typical answer already describes the land as it was about four weeks ago.

Separately, the refresh cron was `0 3 */14 * *`. Day-of-month stepping fires on
the 1st, 15th and 29th and then resets, so the real gap swings between 2 and 17
days while the comment claimed a steady 14.

## Decision

- `water_reach` selects `coalesce(ws.indices_as_of, pz.last_computed)` as
  `observed_at`, and `ReachableWater` carries it.

  **Prefer `indices_as_of`** (added upstream in migration 010). It is the date
  the snapshot was *taken*; `last_computed` is set to `now()` when the transfer
  finishes, so it is the pipeline RUN time. They diverge whenever a refresh runs
  late, and reporting the run time as the observation date overstates freshness
  by exactly the amount that matters. `last_computed` remains the fallback for
  points archived before that column existed.
- Every advisory ends with the observation date.
- Past `SOFTEN_AFTER_DAYS` (21) the wording changes from a date to an explicit
  age plus a caveat: *"Picha ya satelaiti ina siku 47 — hali huenda imebadilika."*
- Cron corrected to `0 3 1,15 * *`.

## Consequences

- This makes the staleness visible to herders, which is uncomfortable and
  correct. It also creates the pressure to fix the refresh cadence and the
  compositing window (Phase 5) that the previous silence removed.
- A hard refusal threshold (decline to assert forage past ~60 days) is specified
  in the plan but not yet implemented; it needs a real distribution of
  `last_computed` ages to pick sensibly. Tracked for R0.

## What would change our mind

Nothing about showing the date. The thresholds are tunable; the transparency is
not negotiable for a product whose promise is timing.
