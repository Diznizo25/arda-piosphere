-- Arda Link — outbound alert ledger (migration 012)
--
-- Why: the system has only ever been able to ANSWER. Nothing reaches a herder
-- who does not ask. The first thing worth saying unprompted needs no satellite
-- and no forecast: when one herder reports a borehole broken, everybody else
-- who relies on that point should be told before they walk there.
--
-- pastoralists.water_source_id already records which point each herder's
-- animals drink from, and it is indexed — so "who relies on this borehole" is a
-- one-line query. Nothing was ever sent.
--
-- This table is the ledger that makes sending SAFE. Every decision is written
-- here, including the decisions NOT to send, because:
--
--   * dedup    a herder must not be told the same thing twice in a fortnight
--   * rate     a herder must not be buried; one bad day of reports must not
--              become ten messages
--   * audit    when a herder says "you told me the pump was fine", we need the
--              record of exactly what was sent, when, and on what evidence
--   * dry-run  the same rows are written with status='dry_run' so an operator
--              can review precisely who WOULD have been messaged before any
--              real send is switched on
--
-- Suppressed rows are the valuable ones. A table containing only successes
-- cannot answer "why didn't this herder hear about it".
--
-- RLS posture unchanged (001-011): enabled + forced, no anon policies.

-- Allow query_log to carry alert activity so the ops dashboard sees it too.
do $$
begin
    alter table query_log drop constraint if exists query_log_kind_check;
    alter table query_log add constraint query_log_kind_check
        check (kind in ('advisory', 'map', 'weight', 'status', 'pin', 'alert', 'other'));
end $$;

create table if not exists alert_log (
  id               uuid primary key default gen_random_uuid(),
  pastoralist_id   uuid not null references pastoralists(id) on delete cascade,
  water_source_id  uuid references water_sources(id) on delete set null,

  alert_type       text not null
                   check (alert_type in ('water_status', 'forage_decline',
                                         'rain_upstream', 'other')),

  -- Identifies the EPISODE, not the moment: 'water_status:<uuid>:<status>'.
  -- Re-reports of the same condition inside the cooldown are suppressed, but
  -- the same point breaking again months later is a new event and does alert.
  event_key        text not null,

  -- 'freeform'  within WhatsApp's 24h service window, no template needed
  -- 'template'  pre-approved Meta template, required outside that window
  channel          text check (channel in ('freeform', 'template')),

  -- Why this herder did or did not hear about it.
  status           text not null
                   check (status in ('sent', 'dry_run', 'failed',
                                     'suppressed_dedup', 'suppressed_rate',
                                     'suppressed_window', 'suppressed_disabled',
                                     'suppressed_self')),

  -- Evidence the alert rested on: report count, reporter, message text.
  detail           jsonb not null default '{}'::jsonb,
  created_at       timestamptz not null default now()
);

-- Dedup and rate-limit lookups are both "recent rows for this herder".
create index if not exists idx_alert_log_person_time
  on alert_log (pastoralist_id, created_at desc);
create index if not exists idx_alert_log_event
  on alert_log (pastoralist_id, event_key, created_at desc);
create index if not exists idx_alert_log_water
  on alert_log (water_source_id, created_at desc);
create index if not exists idx_alert_log_status
  on alert_log (status, created_at desc);

alter table alert_log enable row level security;
alter table alert_log force row level security;
