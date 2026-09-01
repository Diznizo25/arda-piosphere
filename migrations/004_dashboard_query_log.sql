-- Arda Link — live dashboard support (migration 004)
--
-- query_log: append-only request/activity log so the ops dashboard can show
-- live queries (advisories, maps, weights, pins), latencies, and failures.
-- Written by the web service via app/services/query_log.py (fail-open: a
-- logging error never breaks the advisory/map path).
--
-- RLS: same posture as 001-003 — enabled + forced, no anon policies (the
-- backend talks through the service_role key by design).

create table if not exists query_log (
  id              uuid primary key default gen_random_uuid(),
  kind            text not null default 'advisory'
                  check (kind in ('advisory', 'map', 'weight', 'status', 'pin', 'other')),
  phone           text,
  latitude        double precision,
  longitude       double precision,
  species         text check (species in ('cattle', 'shoat', 'camel')),
  water_source_id uuid references water_sources(id) on delete set null,
  result          text check (result in ('ok', 'not_found', 'error')),
  latency_ms      integer,
  detail          jsonb not null default '{}'::jsonb,
  created_at      timestamptz not null default now()
);

create index if not exists idx_query_log_created on query_log (created_at desc);
create index if not exists idx_query_log_kind on query_log (kind);
create index if not exists idx_query_log_water on query_log (water_source_id);

alter table query_log enable row level security;
alter table query_log force row level security;
