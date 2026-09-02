-- Arda Link — water point types (migration 007)
--
-- water_sources.water_type: what kind of water the point actually is
-- (river/borehole/well/spring/pan/tap), so the map shows "is it a river?" and
-- markers/type labels are correct. Sourced from OSM tags at import/backfill
-- time, from WPDx technology fields, or chosen by the herder when they PIN.
--
-- RLS: same posture as 001-006.

alter table water_sources add column if not exists water_type text;

create index if not exists idx_water_sources_type on water_sources (water_type);
