-- Arda Link — index history + located ground truth (migration 011)
--
-- Two additions, both about MEMORY. Neither changes any existing behaviour.
--
-- 1) zone_index_history
--    Every satellite refresh overwrites cogs/<id>/indices.tif, and
--    piosphere_zones.last_computed is a single timestamp, not a series. So the
--    service can only ever report the present. It cannot answer the questions
--    that actually move a herd:
--        is forage here improving or declining, and how fast?
--        at this rate, how many days of grazing are left in this ring?
--        is this water body shrinking year on year?
--    One row per ring per refresh — a few kB — makes all of them computable.
--    The numbers are already being calculated on every refresh and thrown away.
--
-- 2) ground_truth_reports.geom / indices_at_report / satellite_observed_at
--    config/advisory_thresholds.yaml says its numbers are "starting defaults,
--    not validated against ground truth yet". Every herder report is a free
--    labelled sample for exactly that calibration — but a report with no
--    location is not trainable, because herders are mobile and "malisho mabaya"
--    could mean anywhere within a day's walk.
--
--    THIS IS WORTHLESS RETROACTIVELY. Reports already in the table cannot be
--    located after the fact. Only reports from the day this ships onward can
--    carry it, which is why it lands in phase 0 rather than when modelling
--    starts.
--
-- RLS posture unchanged (001-010): enabled + forced, no anon policies.

-- ============================================================================
-- zone_index_history
-- ============================================================================
create table if not exists zone_index_history (
  id                uuid primary key default gen_random_uuid(),
  water_source_id   uuid not null references water_sources(id) on delete cascade,
  species           text not null check (species in ('cattle', 'shoat', 'camel')),

  -- The IMAGERY date, not the time this row was written. These differ whenever
  -- a refresh runs late, and conflating them silently corrupts every trend
  -- computed across the gap — the series would record when the pipeline ran
  -- instead of when the ground looked like that.
  observed_at       timestamptz not null,
  computed_at       timestamptz not null default now(),

  -- Per-band zonal means, e.g. {"NDVI": 0.19, "SATVI": 0.22, ...}. jsonb rather
  -- than columns so adding or retiring a band needs no migration.
  band_means        jsonb not null default '{}'::jsonb,

  -- Share of CLASSIFIABLE pixels per forage class, e.g.
  -- {"green_growing": 0.17, "dry_forage": 0.31, "bare_degraded": 0.52}.
  -- This is what the advisory should report instead of a single ring mean: a
  -- ring that is 17% green riverine strip and 83% bare averages to "bare", a
  -- label that describes no pixel in it.
  class_fractions   jsonb not null default '{}'::jsonb,
  usable_fraction   numeric(4,3),

  -- Real data availability: share of in-ring pixels usable in EVERY band
  -- needed to classify. Not the polygon-area ratio the old ZoneStats returned.
  coverage_ratio    numeric(4,3),
  in_ring_px        integer,
  classified_px     integer,

  -- One observation per ring per imagery date. A re-run of the same refresh
  -- updates in place rather than double-counting the series.
  unique (water_source_id, species, observed_at)
);

create index if not exists idx_zih_point_species_time
  on zone_index_history (water_source_id, species, observed_at desc);
create index if not exists idx_zih_observed
  on zone_index_history (observed_at desc);

alter table zone_index_history enable row level security;
alter table zone_index_history force row level security;

-- ============================================================================
-- Located ground truth
-- ============================================================================
alter table ground_truth_reports
  add column if not exists geom geometry(Point, 4326);

alter table ground_truth_reports
  -- The index vector at that place and time, captured at report. Without it the
  -- label cannot be matched back to what the satellite saw, and the report
  -- teaches nothing.
  add column if not exists indices_at_report jsonb;

alter table ground_truth_reports
  -- Imagery date behind indices_at_report. A label is only as good as the
  -- reading it is paired with; a 40-day-old reading pairs badly.
  add column if not exists satellite_observed_at timestamptz;

alter table ground_truth_reports
  add column if not exists water_source_distance_m numeric(10,1);

create index if not exists idx_gtr_geom
  on ground_truth_reports using gist (geom);
create index if not exists idx_gtr_reported_at
  on ground_truth_reports (reported_at desc);
