-- Arda Link — Piosphere Grazing Advisory
-- 003: personal onboarding, conversation state, weight (heart-girth) tracking.
--
-- Adds:
--   pastoralists.full_name / herd_composition / onboarded_at
--   conversation_state          - WhatsApp state machine rows (onboarding,
--                                 weight flow, pin confirmation)
--   weight_records              - per-animal heart-girth weight estimates
--   herd_estimates              - sampled herd-weight estimates
--
-- RLS: same posture as 001 - enabled + forced, no anon policies (the backend
-- talks through the service_role key by design).

alter table pastoralists add column if not exists full_name text;
alter table pastoralists add column if not exists herd_composition jsonb
    default '{}'::jsonb;  -- e.g. {"cattle": 12, "shoat": 30, "camel": 4}
alter table pastoralists add column if not exists onboarded_at timestamptz;

-- ============================================================================
-- conversation_state: one row per phone for the guided WhatsApp flows.
--   state: onboarding.name | onboarding.language | onboarding.animals |
--          weight.species | weight.girth | weight.age | weight.herd_size |
--          weight.sample | pin.confirm | ...
--   data:  jsonb payload carried across turns of the same flow.
-- ============================================================================
create table if not exists conversation_state (
  phone_number text primary key,
  state        text not null,
  data         jsonb not null default '{}'::jsonb,
  updated_at   timestamptz not null default now()
);

alter table conversation_state enable row level security;
alter table conversation_state force row level security;

-- ============================================================================
-- weight_records: a single measured animal.
-- ============================================================================
create table if not exists weight_records (
  id                  uuid primary key default gen_random_uuid(),
  pastoralist_id      uuid not null references pastoralists(id) on delete cascade,
  species             text not null check (species in ('cattle', 'shoat', 'camel')),
  age_class           text check (age_class in ('young', 'adult')),
  sex                 text check (sex in ('male', 'female')),
  heart_girth_cm      numeric(6,1) not null,
  estimated_weight_kg numeric(7,2) not null,
  method              text not null default 'heart_girth',
  measured_at         timestamptz not null default now()
);

create index if not exists idx_weight_records_pastoralist on weight_records (pastoralist_id, measured_at desc);

alter table weight_records enable row level security;
alter table weight_records force row level security;

-- ============================================================================
-- herd_estimates: sampling-based estimate for a whole herd.
-- ============================================================================
create table if not exists herd_estimates (
  id                 uuid primary key default gen_random_uuid(),
  pastoralist_id     uuid not null references pastoralists(id) on delete cascade,
  species            text not null check (species in ('cattle', 'shoat', 'camel')),
  herd_count         int not null,
  sample_size        int not null,
  sample_mean_kg     numeric(7,2) not null,
  estimated_total_kg numeric(10,2) not null,
  low_estimate_kg    numeric(10,2),
  high_estimate_kg   numeric(10,2),
  estimated_at       timestamptz not null default now()
);

create index if not exists idx_herd_estimates_pastoralist on herd_estimates (pastoralist_id, estimated_at desc);

alter table herd_estimates enable row level security;
alter table herd_estimates force row level security;
