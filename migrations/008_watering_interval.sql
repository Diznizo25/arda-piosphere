-- Arda Link — watering interval (migration 008)
--
-- pastoralists.water_interval: how often the herder waters their animals in the
-- dry season ('daily' | 'every_2_3_days'). A longer watering interval lets
-- animals graze further from water before returning, so the species reach ring
-- is scaled at read time (config/species_rings.yaml -> watering_intervals),
-- capped at the satellite compute ring. NULL = assume daily.
--
-- RLS: same posture as 001-007.

alter table pastoralists add column if not exists water_interval text;

create index if not exists idx_pastoralists_interval on pastoralists (water_interval);
