-- Arda Link — the weekly note's plumbing (migration 013)
--
-- Why these two columns/tables exist:
--
--   pastoralists.last_inbound_at
--       WhatsApp only lets a business send a FREE-FORM message inside the 24-hour
--       window that starts with the herder's last message. Without knowing when he
--       last wrote to us, a weekly send is guesswork: some messages land, some are
--       silently rejected. This column makes the window explicit, so the weekly
--       sender can tell "can message now" from "needs an approved template".
--
--   weekly_notes
--       One row per herder per week, so a retry, a manual run and the scheduled run
--       can never send the same herder the same note twice — and so we can see what
--       actually reached people (the honesty check on our own retention loop).
--
-- RLS posture unchanged (001-012).

alter table pastoralists add column if not exists last_inbound_at timestamptz;

create index if not exists idx_pastoralists_last_inbound
    on pastoralists (last_inbound_at desc);

create table if not exists weekly_notes (
  id              uuid primary key default gen_random_uuid(),
  phone           text not null,
  week_start      date not null,               -- the Monday this note belongs to
  sent_at         timestamptz not null default now(),
  delivered       boolean not null default false,
  skipped_reason  text,                        -- 'outside_24h_window' | 'no_water_point' | ...
  body            text,
  unique (phone, week_start)
);

create index if not exists idx_weekly_notes_week on weekly_notes (week_start desc);

alter table weekly_notes enable row level security;
alter table weekly_notes force row level security;
