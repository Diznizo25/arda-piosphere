-- Arda Link — water-point build tracking (migration 002)
--
-- When a herder pins a water point, the web service registers the row + rings
-- immediately and inserts a build record here. A scheduled GitHub Actions job
-- (build-water-points.yml) claims pending builds, runs the single-point GEE
-- compute -> export -> R2 transfer pipeline, and updates status/progress at
-- each stage. The web service renders a progress-bar image and sends it to the
-- herder on every stage change, so the user is never left hanging.

create table if not exists water_point_builds (
  id              uuid primary key default gen_random_uuid(),
  water_source_id uuid not null unique references water_sources(id) on delete cascade,
  creator_phone   text not null,              -- E.164 of the herder who pinned it
  language        text not null default 'swahili' check (language in ('swahili', 'english')),
  status          text not null default 'pending'
                  check (status in ('pending', 'running', 'done', 'failed')),
  stage           text,                       -- human-readable current stage label
  stage_index     int,                        -- 1-based index of the current stage
  stage_total     int,                        -- total number of stages
  progress        int not null default 0 check (progress >= 0 and progress <= 100),
  error           text,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

alter table water_point_builds enable row level security;
alter table water_point_builds force row level security;

create index if not exists idx_builds_pending on water_point_builds (status);
create index if not exists idx_builds_creator on water_point_builds (creator_phone);

drop trigger if exists trg_builds_updated_at on water_point_builds;
create trigger trg_builds_updated_at
  before update on water_point_builds
  for each row execute function set_updated_at();
