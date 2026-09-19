# ADR 001 — Collect history now, in a separate workspace

**Status:** accepted · **Phase:** 0 · **Date:** 2026-09-18

## Context

Production forgets, and the forgetting is unrecoverable.

- `water_sources.status` is overwritten in place by every herder report.
- Every satellite refresh overwrites `cogs/<id>/indices.tif`.
- `piosphere_zones.last_computed` is a single timestamp, not a series.

So the service can only ever report the present. It cannot answer the questions
that actually move a herd — is forage declining and how fast, how many days of
grazing remain, did that pump repair hold, is this pan shrinking year on year.

Separately, analysis needs credentials. A notebook holding the app's
`SUPABASE_SERVICE_ROLE_KEY` bypasses RLS on a database containing herders'
phone numbers and movement traces, and is the most likely way to damage a
service people depend on for water.

## Decision

1. **Start collecting history immediately**, before any other fix. Two paths,
   deliberately redundant:
   - `migrations/011_history_and_labels.sql` adds `zone_index_history` so the
     app can read its own trend without depending on the lake.
   - `../arda-data` (`ingest cogs`) records the same series into the lake by
     reading the COGs that already exist — **no migration, no app change, no
     write access to production**. This is what makes collection startable on
     day one rather than after a deploy.
2. **Analysis lives in a separate repository** with a read-only Postgres role,
   a lake-scoped storage key, and an `assert_read_only()` guard that refuses to
   start if a write-capable production credential is present.

## Why now rather than later

Almost everything else in the review gets no more expensive by waiting. This
does. A day not collected is a day that will never exist, and the first
question anyone will ask of a forage model is "what happened last season".

## Relationship to the dated COG archive

Upstream solved the adjacent half of this problem in migration 010: every
refresh now also copies the raster to `cogs/<id>/archive/indices_<date>.tif`
(`storage.archive_current_cog`) and records `water_sources.indices_as_of`.

The two are complementary, not duplicates. The archive keeps the **rasters**, so
any future statistic can be recomputed from the real pixels. `zone_index_history`
keeps the **computed zone statistics**, so a trend is one SQL query rather than
re-reading and re-classifying hundreds of megabytes per point per date. Keep
both: the archive is the source of truth, the table is the working series.

## Consequences

- Two writers for the same series. Accepted: the lake is the analysis surface,
  the table is what the app reads. They are compared in the Phase 1 review.
- `ground_truth_reports` gains `geom`, `indices_at_report` and
  `satellite_observed_at`. **Worthless retroactively** — reports already stored
  cannot be located after the fact, so only reports from this migration onward
  can train anything. That is the whole reason it lands in Phase 0.
- One production table, `conversation_state`, is deliberately never ingested: a
  live scratchpad of raw message text with no analytical value.

## What would change our mind

If the lake and the in-app table diverge in a way that is not explained by
timing, drop the in-app table and read the lake — one writer is better than two
that disagree.
