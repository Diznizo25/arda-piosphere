-- Arda Link — herder water-point confirmation (migration 005)
--
-- pastoralists.water_source_id: the water point the herder CONFIRMED their
-- animals drink from (chosen from the named nearby list during onboarding, or
-- set automatically when they PIN a new point). The system "remembers" the
-- herder: maps/advisories then center on their own water point by default.
--
-- RLS: same posture as 001-004.

alter table pastoralists add column if not exists water_source_id uuid
    references water_sources(id) on delete set null;

create index if not exists idx_pastoralists_water on pastoralists (water_source_id);
