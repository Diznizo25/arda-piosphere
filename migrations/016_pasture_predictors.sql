-- ============================================================================
-- 016 — pasture predictors, and the forecast we actually issued
--
-- WHY THIS MIGRATION EXISTS
--   Migration 015 started the clock: one row per water point, species and day.
--   But the row kept only the CLASS MIX (five whole percentages), which is a
--   scalar to fit a line through and nothing more. Everything that could have
--   predicted the collapse was thrown away as soon as the sentence was sent:
--     * the index means behind the classes (NDVI/SATVI/BSI/NDMI/VCI) — NDMI, in
--       particular, falls while grass is curing, 2-4 weeks BEFORE the class mix
--       moves from "dry forage" to "bare";
--     * the near-water vs outer-ring contrast — grazing pressure is highest
--       close to the water point, so a ring being eaten from the water outward
--       shows a gap weeks before its average share collapses;
--     * the patch structure — a ring of scattered specks is already unusable to
--       a herd even when the TOTAL usable share still looks healthy;
--     * how dry the period was when the sample was taken (rain in the previous
--       14 days, soil moisture), because the collapse rate while dry is the rate
--       a herder is walking into, and mixing it with wet-season green-up is what
--       made a collapsing ring read as "stable".
--
--   No herd counts are added, deliberately. Registration is slow, so a head
--   count is missing for most herds for a long time; a predictor that needs one
--   would be silent exactly where it is needed. The ring's own SPATIAL signature
--   (near vs far, patch size) stands in for grazing pressure instead, and it is
--   measured for every herd the moment they have a water point. When a herd
--   count does exist (grazing_events.head_count) it is a refinement, never a
--   prerequisite.
--
--   depletion_forecasts keeps what we SAID, not just what we measured. Without
--   it a forecast can never be scored: "18-25 days" becomes folklore, and the
--   thresholds that produce it (RUNOUT_PCT, NOISE_PP_PER_WEEK, the ±25% band)
--   can only ever be guessed. scripts/backtest_depletion.py fills outcome in.
-- ============================================================================

-- --- what the ring looked like, not just how it classified -------------------
alter table ring_pasture_samples add column if not exists ndvi        numeric(5,3);
alter table ring_pasture_samples add column if not exists satvi       numeric(5,3);
alter table ring_pasture_samples add column if not exists bsi         numeric(5,3);
-- NDMI is the curing signal: grass loses water before it loses reflectance.
alter table ring_pasture_samples add column if not exists ndmi        numeric(5,3);
alter table ring_pasture_samples add column if not exists vci         numeric(5,1);
-- Was this reading taken in a dry spell? The rate that matters is the rate dry.
alter table ring_pasture_samples add column if not exists soil_moisture numeric(8,4);
alter table ring_pasture_samples add column if not exists rain_14d_mm   numeric(8,2);
-- Grazing pressure without a head count: the piosphere's own shape.
alter table ring_pasture_samples add column if not exists near_usable_pct smallint;
alter table ring_pasture_samples add column if not exists far_usable_pct  smallint;
-- Fragmentation: share of the ring in its largest connected usable patch, and
-- how many patches of any size there are.
alter table ring_pasture_samples add column if not exists patch_pct   smallint;
alter table ring_pasture_samples add column if not exists patch_count integer;
-- When the SATELLITE picture was taken (not when the row was written). A stale
-- COG must never masquerade as this morning's reading.
alter table ring_pasture_samples add column if not exists snapshot_as_of date;

-- --- interval belongs in the key --------------------------------------------
-- The ring GROWS when a herder waters every 2-3 days instead of daily
-- (config/species_rings.yaml: cattle 7.0 km -> 9.8 km, i.e. 154 km² -> 302 km²).
-- The old key (point, species, day) welded two different footprints into one
-- trend line and let one silently overwrite the other on the same day.
alter table ring_pasture_samples
    drop constraint if exists ring_pasture_samples_water_source_id_species_sampled_on_key;
create unique index if not exists uq_ring_pasture_samples_day
    on ring_pasture_samples (water_source_id, species, interval, sampled_on);


-- ============================================================================
-- depletion_forecasts: one row per ring per day — what we told him, and what we
-- will be judged on. Distinct from depletion_alerts, which is a dedupe ledger
-- keyed on the week we warned about (it must not be reused for scoring).
-- ============================================================================
create table if not exists depletion_forecasts (
  id              bigserial primary key,
  water_source_id uuid not null references water_sources(id) on delete cascade,
  species         text not null check (species in ('cattle', 'shoat', 'camel')),
  interval        text not null default 'daily',
  issued_on       date not null,
  status          text not null,           -- no_history | stable | already_low | projected | beyond_horizon
  usable_pct      smallint,
  -- the rate we projected with, and the dry-period rate it came from (nullable:
  -- a ring can have a trend with no dry-period samples yet)
  slope_pp_week      numeric(4,1),
  dry_slope_pp_week  numeric(4,1),
  rate_source        text,                 -- all | dry
  days_left       integer,
  days_left_lo    integer,
  days_left_hi    integer,
  samples         smallint,
  span_days       smallint,
  confidence      text,                    -- high | moderate | low
  -- the WHY, as far as the ring's shape can support it (see depletion.pressure)
  cause           text,                    -- season | grazing | unknown
  gap_pp          smallint,                -- far_pct - near_pct at issue time
  curing          boolean default false,   -- NDMI falling before the class mix moved
  snapshot_as_of  date,
  created_at      timestamptz not null default now(),
  -- filled in later by scripts/backtest_depletion.py
  scored_on       date,
  outcome         text check (outcome in ('hit', 'early', 'late', 'no_collapse', 'unknown')),
  runout_on       date,
  unique (water_source_id, species, interval, issued_on)
);

create index if not exists idx_depletion_forecasts_unscored
  on depletion_forecasts (issued_on) where outcome is null;

alter table depletion_forecasts enable row level security;
-- Same posture as 001-015: no policies, flag forced; the service role bypasses.
alter table depletion_forecasts force row level security;
