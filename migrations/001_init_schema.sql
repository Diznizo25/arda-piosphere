-- Arda Link — Piosphere Grazing Advisory
-- Phase 1 schema: relational/vector tables only. Dense per-pixel index data lives
-- in COGs in object storage, never in Postgres rows.
--
-- RLS design note: this database has no direct client (browser/mobile) access —
-- pastoralists only ever interact through WhatsApp, proxied by the FastAPI backend,
-- which connects using the Supabase service_role key (bypasses RLS by design).
-- RLS is enabled on every table below with NO permissive policies for anon/
-- authenticated roles, so if a client key is ever leaked or misused, the default
-- posture is deny-all rather than the previous system's "RLS disabled" mistake.
-- If a future admin dashboard needs direct client reads, add scoped SELECT
-- policies then — don't loosen this file speculatively.

create extension if not exists postgis;
create extension if not exists pgcrypto; -- for gen_random_uuid()

-- ============================================================================
-- water_sources: unified water point layer, one row per known water point
-- regardless of where it was sourced from.
-- ============================================================================
create table if not exists water_sources (
  id              uuid primary key default gen_random_uuid(),
  geom            geometry(Point, 4326) not null,
  source_type     text not null check (source_type in ('satellite_gsw', 'osm', 'wpdx', 'ilri', 'ground_truth')),
  source_ref      text,               -- original id/ref in the source dataset, for traceability
  ward            text,               -- Isiolo ward name, denormalized for fast filtering/QA
  county          text not null default 'Isiolo',
  confidence      numeric(3,2) not null default 0.50 check (confidence >= 0 and confidence <= 1),
  last_confirmed  timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index if not exists idx_water_sources_geom on water_sources using gist (geom);
create index if not exists idx_water_sources_ward on water_sources (ward);
create index if not exists idx_water_sources_source_type on water_sources (source_type);

alter table water_sources enable row level security;
alter table water_sources force row level security;

-- ============================================================================
-- piosphere_zones: species-specific buffer ring per water point.
-- Three rows per water_source (cattle / shoat / camel), each with its own
-- radius and buffered polygon. GEE compute runs once at the outer (camel)
-- radius; narrower species rings are tagged from that same result at read
-- time rather than recomputed.
-- ============================================================================
create table if not exists piosphere_zones (
  id              uuid primary key default gen_random_uuid(),
  water_source_id uuid not null references water_sources(id) on delete cascade,
  species         text not null check (species in ('cattle', 'shoat', 'camel')),
  radius_km       numeric(5,2) not null,
  geom            geometry(Polygon, 4326) not null,
  last_computed   timestamptz,
  created_at      timestamptz not null default now(),
  unique (water_source_id, species)
);

create index if not exists idx_piosphere_zones_geom on piosphere_zones using gist (geom);
create index if not exists idx_piosphere_zones_water_source on piosphere_zones (water_source_id);
create index if not exists idx_piosphere_zones_species on piosphere_zones (species);

alter table piosphere_zones enable row level security;
alter table piosphere_zones force row level security;

-- ============================================================================
-- pastoralists: registered WhatsApp users of the system.
-- ============================================================================
create table if not exists pastoralists (
  id                  uuid primary key default gen_random_uuid(),
  phone_number        text not null unique,   -- E.164, WhatsApp wa_id
  preferred_language  text not null default 'borana' check (preferred_language in ('borana', 'swahili')),
  primary_species     text check (primary_species in ('cattle', 'shoat', 'camel')),
  last_known_location geometry(Point, 4326),
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

create index if not exists idx_pastoralists_phone on pastoralists (phone_number);

alter table pastoralists enable row level security;
alter table pastoralists force row level security;

-- ============================================================================
-- ground_truth_reports: minimal herder feedback loop.
-- ============================================================================
create table if not exists ground_truth_reports (
  id              uuid primary key default gen_random_uuid(),
  pastoralist_id  uuid not null references pastoralists(id) on delete cascade,
  water_source_id uuid references water_sources(id) on delete set null,
  report_type     text not null check (report_type in ('water_dry', 'water_available', 'pasture_good', 'pasture_poor', 'other')),
  report_text     text,
  reported_at     timestamptz not null default now()
);

create index if not exists idx_ground_truth_water_source on ground_truth_reports (water_source_id);
create index if not exists idx_ground_truth_pastoralist on ground_truth_reports (pastoralist_id);

alter table ground_truth_reports enable row level security;
alter table ground_truth_reports force row level security;

-- ============================================================================
-- updated_at triggers
-- ============================================================================
create or replace function set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists trg_water_sources_updated_at on water_sources;
create trigger trg_water_sources_updated_at
  before update on water_sources
  for each row execute function set_updated_at();

drop trigger if exists trg_pastoralists_updated_at on pastoralists;
create trigger trg_pastoralists_updated_at
  before update on pastoralists
  for each row execute function set_updated_at();
