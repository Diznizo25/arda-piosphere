-- Arda Link — named water points (migration 006)
--
-- water_sources.name: the local name a herder can recognise a water point by
-- (e.g. "Oldonyiro borehole", "Ngaremara well"). Sourced from OSM/WPDx names at
-- import time, or given by the herder when they PIN a new point. Used as the
-- primary label in the confirmation list, map markers and advisories — ward
-- names alone don't identify a water point to a pastoralist.
--
-- RLS: same posture as 001-005.

alter table water_sources add column if not exists name text;

create index if not exists idx_water_sources_name on water_sources (name);
