-- Arda Link — pest and parasite risk windows (migration 011)
--
-- Why this exists: parasites and pests are the safest thing we can forecast. The
-- environment-to-pest link is observable by the herder himself (he can SEE ticks),
-- the advice is observation rather than treatment, and being wrong costs a wasted
-- look at the animals rather than a panic. It also addresses production losses
-- (weight and milk) rather than a disease event.
--
-- Adds:
--   environment_daily.temperature_max_c / temperature_min_c / humidity
--       the weather drivers the pest rules need. We already stored rain and soil
--       moisture; ticks and worm larvae need warmth as well as moisture.
--   pest_observations
--       what the herder actually sees ("kupe wengi" / "sikuona"), captured in one
--       tap. This is the calibration loop: it is the only ward-level pest ground
--       truth anyone is collecting, and it is what turns a rule of thumb into a
--       local model.
--
-- RLS: same posture as 001-010 — enabled + forced, no anon policies (the backend
-- talks through the service_role key by design).

alter table environment_daily add column if not exists temperature_max_c numeric(5,2);
alter table environment_daily add column if not exists temperature_min_c numeric(5,2);
alter table environment_daily add column if not exists humidity numeric(5,2);

-- ============================================================================
-- pest_observations: one row per herder look-at-the-animals reply.
--   pest_key: ticks | worms | flies | hooves
--   seen:     true (they saw the pest/sign), false (they looked and saw nothing)
-- A "looked and saw nothing" reply is as valuable as a positive one: it is what
-- keeps the window honest instead of turning every alert into a self-fulfilling
-- story.
-- ============================================================================
create table if not exists pest_observations (
  id              uuid primary key default gen_random_uuid(),
  phone           text,
  water_source_id uuid references water_sources(id) on delete set null,
  pest_key        text not null,
  seen            boolean not null,
  note            text,
  observed_on     date not null default current_date,
  created_at      timestamptz not null default now()
);

create index if not exists idx_pest_obs_pest on pest_observations (pest_key, observed_on desc);
create index if not exists idx_pest_obs_water on pest_observations (water_source_id);

alter table pest_observations enable row level security;
alter table pest_observations force row level security;
