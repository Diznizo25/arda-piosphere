-- ============================================================================
-- 015 — pasture history & depletion alerts
--
-- WHY THIS MIGRATION EXISTS
--   "Malisho yataisha lini?" cannot be answered from a single satellite image.
--   The pasture class mix inside a species ring is read from the 8x overview COG
--   (map_renderer.pasture_overlay_status); until now that read was thrown away
--   after one reply, so every forecast restarted from zero.
--
--   ring_pasture_samples keeps one row per water point, species and day. The COGs
--   themselves are refreshed every 14 days, so consecutive weeks can carry the
--   same value — that is correct and it is why the trend fit refuses to invent a
--   decline (see app/services/depletion.py).
--
--   depletion_alerts is the dedupe ledger for the unprompted push. It is keyed on
--   the DIVERGENCE WE WARNED ABOUT (point, species, projected run-out week), not
--   on the calendar week: a forecast that says "18-25 days" must not be re-sent
--   every Monday, and a forecast that moves materially closer must not be
--   suppressed because "we already sent one this week".
-- ============================================================================

create table if not exists ring_pasture_samples (
  id              bigserial primary key,
  water_source_id uuid not null references water_sources(id) on delete cascade,
  species         text not null check (species in ('cattle', 'shoat', 'camel')),
  -- The watering interval a sample was framed with: the species ring grows when a
  -- herd waters every 2-3 days (config/species_rings.yaml), so a sample taken at
  -- 'every_2_3_days' covers more ground than one taken at 'daily'.
  interval        text not null default 'daily',
  sampled_on      date not null,
  usable_pct      smallint check (usable_pct between 0 and 100),
  green_pct       smallint check (green_pct between 0 and 100),
  dry_pct         smallint check (dry_pct between 0 and 100),
  bare_pct        smallint check (bare_pct between 0 and 100),
  blank_pct       smallint check (blank_pct between 0 and 100),
  created_at      timestamptz not null default now(),
  unique (water_source_id, species, sampled_on)
);

create index if not exists idx_ring_pasture_samples_lookup
  on ring_pasture_samples (water_source_id, species, sampled_on desc);

alter table ring_pasture_samples enable row level security;
-- Same convention as every other table here (001..013): no policies, and the flag
-- is forced so a stray table owner cannot read a herder's history either. The app
-- connects as a role with BYPASSRLS (checked: pg_roles.rolbypassrls = true), which
-- is why writes from the service still land.
alter table ring_pasture_samples force row level security;

create table if not exists depletion_alerts (
  id              bigserial primary key,
  water_source_id uuid not null references water_sources(id) on delete cascade,
  species         text not null check (species in ('cattle', 'shoat', 'camel')),
  -- The first day of the projected run-out window we warned about (~ISO week of
  -- that date). The unique key is what makes the sender idempotent.
  runout_week     date not null,
  first_sent_at   timestamptz not null default now(),
  recipients      int not null default 0,
  unique (water_source_id, species, runout_week)
);

alter table depletion_alerts enable row level security;
alter table depletion_alerts force row level security;

