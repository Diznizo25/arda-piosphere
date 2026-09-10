-- Arda Link — water-point STATUS from herder ground truth (migration 009)
--
-- Why: the satellite shows a point EXISTS (GSW recurrence) but not whether the
-- pump is broken, the trough silted or the pan dry TODAY. Only the herder
-- standing there knows. This migration stores that answer on the water point so
-- guidance can stop sending herds to dead water:
--
--   water_sources.status           'unknown'|'flowing'|'functional'|'intermittent'
--                                  |'dry'|'broken'|'not_found'
--   water_sources.status_updated_at when the status was last reported
--   water_sources.status_source     'herder'|'satellite'|'import'
--   water_sources.status_reports    how many independent herder reports back it
--
-- Also: pastoralists.last_advisory_water_source_id — the point we last gave
-- advice about, so a bare "2" (imekauka) reply knows WHICH water it refers to.
-- And ground_truth_reports.report_type gains the new report types.
--
-- RLS posture unchanged (001-008).

alter table water_sources add column if not exists status text not null default 'unknown';
alter table water_sources add column if not exists status_updated_at timestamptz;
alter table water_sources add column if not exists status_source text default 'unknown';
alter table water_sources add column if not exists status_reports integer not null default 0;

do $$
begin
    alter table water_sources add constraint water_sources_status_check
        check (status in ('unknown','flowing','functional','intermittent',
                          'dry','broken','not_found'));
exception when duplicate_object then null;
end $$;

create index if not exists idx_water_sources_status on water_sources (status);

-- The point the last advisory was about (target for one-tap status replies).
alter table pastoralists add column if not exists last_advisory_water_source_id uuid
    references water_sources(id) on delete set null;
alter table pastoralists add column if not exists last_advisory_at timestamptz;

-- Allow the richer report vocabulary (broken pump / point no longer exists).
do $$
begin
    alter table ground_truth_reports drop constraint if exists ground_truth_reports_report_type_check;
    alter table ground_truth_reports add constraint ground_truth_reports_report_type_check
        check (report_type in ('water_dry','water_available','water_flowing',
                               'water_intermittent','water_broken','water_not_found',
                               'pasture_good','pasture_poor','other'));
end $$;

-- Backfill: everything imported starts as unknown, nothing pretends to be verified.
update water_sources set status = 'unknown' where status is null;
