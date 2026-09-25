-- Arda Link — the water loop (migration 011)
--
-- Why: migration 009 let a herder tell us a water point's STATUS. But a status
-- that only the reporter ever sees is a private note, not a network. This
-- migration adds the three things that turn one herder's tap into value for the
-- herders around him:
--
--   1. FAN-OUT  — when a status lands, tell the other herders who drink from the
--      SAME point (water_notices dedupes, so nobody is told twice in 12h).
--   2. QUEUE    — "how long is the line?" as a second one-tap digit
--      (ground_truth_reports.queue_level / water_sources.queue_level). A tank
--      that is full but has 40 herds queued is NOT the same as an empty one.
--   3. REPAIR   — "imekarabatiwa" closes the loop: a repaired point stops
--      suppressing guidance immediately instead of waiting out the 60-day
--      stale window, and the herders who were told it was broken get told it is
--      back.
--
-- RLS posture unchanged (001-010).

-- --- 2. queue ---------------------------------------------------------------
alter table ground_truth_reports add column if not exists queue_level text;

do $$
begin
    alter table ground_truth_reports add constraint ground_truth_reports_queue_check
        check (queue_level is null or queue_level in ('short','long'));
exception when duplicate_object then null;
end $$;

alter table water_sources add column if not exists queue_level text not null default 'unknown';
alter table water_sources add column if not exists queue_updated_at timestamptz;
alter table water_sources add column if not exists queue_reports integer not null default 0;

do $$
begin
    alter table water_sources add constraint water_sources_queue_check
        check (queue_level in ('unknown','short','long'));
exception when duplicate_object then null;
end $$;

-- --- 3. repair closure ------------------------------------------------------
alter table water_sources add column if not exists repaired_at timestamptz;

do $$
begin
    alter table ground_truth_reports drop constraint if exists ground_truth_reports_report_type_check;
    alter table ground_truth_reports add constraint ground_truth_reports_report_type_check
        check (report_type in ('water_dry','water_available','water_flowing',
                               'water_intermittent','water_broken','water_not_found',
                               'water_repaired','pasture_good','pasture_poor','other'));
end $$;

-- --- 1. fan-out log ---------------------------------------------------------
-- One row per (point, phone, kind): the record of what we told whom, which is
-- also the cooldown that keeps this from becoming a broadcast channel.
create table if not exists water_notices (
  id              uuid primary key default gen_random_uuid(),
  water_source_id uuid not null references water_sources(id) on delete cascade,
  phone           text not null,
  kind            text not null,          -- 'status_fanout' | 'repair'
  status          text,                   -- the status that triggered it
  created_at      timestamptz not null default now()
);

create index if not exists idx_water_notices_bucket
    on water_notices (water_source_id, phone, kind, created_at desc);

alter table water_notices enable row level security;
alter table water_notices force row level security;
