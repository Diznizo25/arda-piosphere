# Arda Link — Piosphere Grazing Advisory

Satellite-driven grazing and water advisory for pastoralists in Kenya's arid
and semi-arid lands, delivered over WhatsApp in Borana and Swahili.

A herder shares their location and their animals (cattle, shoat, or camel)
over WhatsApp. Arda Link tells them where the nearest usable water is, and
what grazing condition looks like around it — scoped to how far *their*
animals can actually travel, not a generic radius. Phase 1 covers Isiolo
county, Kenya.

## Why this exists

Most satellite vegetation monitoring (NDVI and similar "greenness" indices)
misreads dry rangeland as bare, dead land. In Kenya's ASALs, the forage that
matters most of the year is standing *cured, dry* grass — not green
vegetation — and NDVI-only tools tell a herder that good dry forage looks the
same as dirt. Arda Link corrects for this by computing SATVI (a soil-adjusted
index that reads both green and dry/senescent vegetation) alongside NDVI, so
the advisory can tell "dry forage available" apart from "actually bare
ground." See `app/services/advisory_logic.py` and `app/services/gee_indices.py`
for the detail.

It also splits water reach by species: cattle, shoats, and camels can travel
very different distances from a water point, so the same water source can be
"reachable" for a camel herder and unreachable for a cattle herder standing
in the same spot.

## How it works

```
map water sources → buffer species-specific reach rings → compute satellite
indices inside those rings → deliver over WhatsApp → capture herder feedback
```

Everything satellite-related is precomputed on a schedule via Google Earth
Engine and stored as Cloud-Optimized GeoTIFFs in object storage. The live
WhatsApp-facing backend never calls Earth Engine directly — it only does fast
windowed reads against precomputed data, so replies come back in seconds.

## Stack

- **Backend:** Python / FastAPI
- **Relational + spatial data:** Postgres/PostGIS (Supabase), RLS enabled on every table
- **Satellite compute:** Google Earth Engine, scheduled batch jobs
- **Raster storage:** Cloud-Optimized GeoTIFFs on Cloudflare R2 (S3-compatible)
- **Delivery:** WhatsApp Business Cloud API

## Project layout

```
app/
  config.py                 # settings + tunable config loaders
  db.py                      # Supabase client + PostGIS connection
  main.py                    # FastAPI app
  models/schemas.py          # request/response models
  routers/                   # advisory, whatsapp webhook, ground-truth endpoints
  services/
    water_reach.py            # species-scoped nearest-reachable-water query
    raster_read.py            # windowed COG reads + zonal stats
    advisory_logic.py         # SATVI/BSI-aware forage classification
    advisory_service.py       # ties the read path together
    i18n.py                   # Borana/Swahili message templates
    whatsapp_client.py        # WhatsApp send helpers
    gee_indices.py            # Earth Engine index math
    storage.py                 # R2 COG storage
scripts/
  import_water_sources.py     # WPDx + OSM import, ward/county-parameterized
  generate_piosphere_zones.py # species buffer rings in PostGIS
  gee_compute_export.py       # scheduled satellite compute + COG export job
migrations/                   # PostGIS schema
config/                       # tunable ring radii + advisory thresholds
```

## Status

Phase 1 (this build) covers the core loop above for Isiolo county. Currently
in the one-ward validation stage before scaling to the full county. Not yet
built: drought early-warning, market signaling, peer connection, vet
registry, marketplace — those are later phases.

Known open items:

- Borana message templates (`app/services/i18n.py`) are a first draft and
  need review by a native speaker before relying on them in the field.
- Species ring radii and forage-condition thresholds (`config/*.yaml`) are
  starting defaults, meant to be tuned from real ground-truth feedback.
- Map image generation for WhatsApp replies isn't built yet (text only).

## Getting started

```bash
cp .env.example .env   # fill in Supabase, R2, Earth Engine, WhatsApp credentials
pip install -r requirements.txt

# apply the schema
# run migrations/001_init_schema.sql against your Postgres/PostGIS database

uvicorn app.main:app --reload
```

One-ward validation pipeline:

```bash
python scripts/import_water_sources.py --boundary config/wards/<ward>.geojson --ward "<Ward>" --source both
python scripts/generate_piosphere_zones.py --ward "<Ward>"
python scripts/gee_compute_export.py --ward "<Ward>"
```

Test the advisory endpoint directly:

```bash
curl -X POST localhost:8000/advisory -H 'Content-Type: application/json' \
  -d '{"lat": 0.35, "lon": 37.58, "species": "camel", "language": "swahili"}'
```
