-- Arda Link — manyatta registry + grazing ledger (migration 014)
--
-- Why this exists: the two things that thin a pastoral herd are the same two
-- things the herder cannot see from where he stands — how much forage is actually
-- over the ridge, and how much energy the walk there costs. Every other feature we
-- have answers a question; this one answers "where did they graze, what was that
-- grass worth, and what closes the gap".
--
-- Adds:
--   manyattas
--       the homestead the herd walks out from. Registered ONCE, from the herder's
--       own location pin, and reused forever after — the walk distance is measured
--       from here (or from his water point until the manyatta is known). This is
--       the "mentor remembers" rule made structural: we never ask twice.
--   pastoralists.manyatta_id
--       points at that row, so the origin of every later walk is one join away.
--   grazing_events
--       one row per "where we grazed today" pin (WhatsApp location, a tapped point
--       on /mapview, or a named place). Stores the pin, the derived walk, the
--       satellite-derived quality band, the energy ledger and the advice code —
--       because a band we showed a herder must stay auditable afterwards, and
--       because the pile of these rows is the only calibration set for `biomass`
--       that will ever exist without a research project.
--
-- Honesty notes that shape the columns:
--   * biomass_*_kg_ha are stored as a BAND (lo/hi), never as a point value.
--   * snapshot_as_of is stored with every row: a reading without its date is a
--     lie we can no longer audit.
--   * herder_quality is the herder's OWN one-tap answer about the same patch. It
--     is ground truth for the satellite classes (ground_truth_reports already
--     holds the same signal; this column keeps it attached to the walk it grades).
--
-- RLS: same posture as 001-013 — enabled + forced, no anon policies (the backend
-- talks through the service_role key by design).

create table if not exists manyattas (
  id              uuid primary key default gen_random_uuid(),
  phone           text,
  name            text,
  geom            geometry(Point, 4326) not null,
  ward            text,
  county          text default 'Isiolo',
  -- how we learned the spot: herder pin | landmark name | carried over from the
  -- water point he confirmed (weaker, so it is recorded honestly)
  source          text not null default 'pin'
                  check (source in ('pin', 'map', 'landmark', 'water_point')),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index if not exists idx_manyattas_phone on manyattas (phone);
create index if not exists idx_manyattas_geom on manyattas using gist (geom);

alter table manyattas enable row level security;
alter table manyattas force row level security;

alter table pastoralists add column if not exists manyatta_id uuid
    references manyattas(id) on delete set null;

create index if not exists idx_pastoralists_manyatta on pastoralists (manyatta_id);

create table if not exists grazing_events (
  id              uuid primary key default gen_random_uuid(),
  phone           text,
  pastoralist_id  uuid,
  manyatta_id     uuid references manyattas(id) on delete set null,
  water_source_id uuid references water_sources(id) on delete set null,
  species         text check (species in ('cattle', 'shoat', 'camel')),
  head_count      integer,
  -- where he says they grazed, and how he told us
  geom            geometry(Point, 4326) not null,
  source          text not null default 'pin'
                  check (source in ('pin', 'map', 'landmark')),
  -- the walk we used (straight-line manyatta -> pin, doubled when round trip):
  -- stored so a wrong distance can be argued with later
  walk_km         numeric(6,1),
  origin_km       numeric(6,1),   -- straight line, before any round-trip factor
  from_manyatta   boolean default false,  -- false = we fell back to the water point
  -- satellite side (all bands, never point values)
  snapshot_as_of  date,
  forage_class    text,
  biomass_lo_kg_ha integer,
  biomass_hi_kg_ha integer,
  utilisable_lo_kg_ha integer,
  utilisable_hi_kg_ha integer,
  me_mj_per_kg_dm numeric(4,1),
  -- energy ledger (MJ ME per head per day; negative balance = losing condition)
  required_mj     numeric(6,1),
  intake_mj       numeric(6,1),
  balance_mj      numeric(6,1),
  balance_lo_mj   numeric(6,1),
  balance_hi_mj   numeric(6,1),
  verdict         text check (verdict in ('surplus', 'holding', 'deficit', 'unknown')),
  advice_code     text,            -- move | water | mineral | feed | sell | none
  supplement_kg_per_head numeric(5,2),
  -- the herder's own one-tap answer about the same patch (1 good / 2 fair / 3 poor)
  herder_quality  text check (herder_quality in ('good', 'fair', 'poor')),
  lang            text default 'swa',
  created_at      timestamptz not null default now()
);

create index if not exists idx_grazing_events_phone on grazing_events (phone, created_at desc);
create index if not exists idx_grazing_events_created on grazing_events (created_at desc);
create index if not exists idx_grazing_events_geom on grazing_events using gist (geom);
create index if not exists idx_grazing_events_point on grazing_events (water_source_id);

alter table grazing_events enable row level security;
alter table grazing_events force row level security;

-- ============================================================================
-- graze_tokens: the identity behind a WhatsApp map link.
--
-- The herder taps a point on /mapview to tell us where he grazed, and the page
-- then has to tell the backend WHO tapped — without putting a phone number in a
-- URL that gets forwarded to a clan group. So the link carries a short opaque
-- token, minted when we send it and consumed by POST /grazing/pin.
--
-- Short-lived by design (48 h): a token is a pointer to one answer, not an
-- identity or a session.
-- ============================================================================
create table if not exists graze_tokens (
  token       text primary key,
  phone       text not null,
  created_at  timestamptz not null default now(),
  expires_at  timestamptz not null default now() + interval '48 hours'
);

create index if not exists idx_graze_tokens_phone on graze_tokens (phone, created_at desc);

alter table graze_tokens enable row level security;
alter table graze_tokens force row level security;
