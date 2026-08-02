# Arda Link — Piosphere Grazing Advisory (Phase 1)

Tells a pastoralist in Isiolo county, over WhatsApp and in their own language,
where the nearest reachable water and grazing land is for their specific
animals — using precomputed satellite indices, not live GIS.

## What's built

```
app/
  config.py               # settings + species-ring / advisory-threshold loaders
  db.py                    # Supabase client + raw PostGIS connection
  main.py                  # FastAPI app, wires all routers
  models/schemas.py        # request/response Pydantic models
  routers/
    advisory.py            # POST /advisory — direct HTTP testing endpoint
    whatsapp.py             # WhatsApp webhook (verify + receive)
    ground_truth.py         # POST /ground-truth — direct HTTP testing endpoint
  services/
    water_reach.py          # species-scoped nearest-reachable-water PostGIS query
    raster_read.py          # windowed rasterio reads + zonal stats from COGs
    advisory_logic.py       # SATVI/BSI-aware forage classification (the core logic)
    advisory_service.py     # ties water_reach + raster_read + advisory_logic + i18n
    i18n.py                 # Borana/Swahili message templates (see warning below)
    whatsapp_client.py       # Meta Graph API send helpers
    pastoralists.py          # pastoralist CRUD (species/language/location state)
    ground_truth.py          # keyword-based feedback classification + confidence update
    gee_indices.py            # Earth Engine index math (NDVI/NDRE/SATVI/BSI/NDMI/NDWI/VCI/GSW)
    gee_auth.py               # EE service-account init
    storage.py                # R2/S3 COG upload + canonical per-water-point key
scripts/
  import_water_sources.py     # WPDx + OSM import, parameterized by ward/county
  generate_piosphere_zones.py # cattle/shoat/camel buffer rings in PostGIS
  gee_compute_export.py       # the scheduled GEE job -> GCS staging -> R2
migrations/001_init_schema.sql  # PostGIS tables + RLS
config/
  species_rings.yaml          # tunable ring radii (cattle/shoat/camel)
  advisory_thresholds.yaml    # tunable SATVI/BSI/NDVI/VCI/GSW thresholds
  wards/README.md              # where ward boundary files go (none bundled yet)
```

All Python files pass `py_compile`. Full dependency install/import testing
needs to happen in your own environment (`pip install -r requirements.txt`) —
some packages (rasterio, earthengine-api, osmium) are heavy enough that this
sandbox couldn't finish installing them in time.

## Build order followed

Matches the spec's validation-gate-first plan: schema → water import → zone
generation → GEE compute/export → backend read path/WhatsApp, all
ward-parameterized from the start so the exact same scripts re-run for the
full-county scale-up. Ground-truth capture is wired into the WhatsApp handler
already (step 9), ahead of full-county QA (step 10), since it shares code with
day 1-4 validation.

## What I need from you to actually run this

1. **Supabase project** — a Postgres project with the PostGIS extension
   available. I need `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and the
   direct Postgres connection string (`DATABASE_URL`, from Settings →
   Database). Run `migrations/001_init_schema.sql` against it once.
2. **Cloudflare R2 bucket** (or any S3-compatible bucket) for COGs —
   account ID, access key, secret key, bucket name, endpoint URL.
3. **Google Earth Engine service account** — a GCP service account
   registered for EE access, its JSON key, and a **separate GCS bucket** for
   staging exports (GEE can only batch-export to Drive/EE-assets/GCS, not
   directly to R2 — the export script copies GCS → R2 automatically after
   each task finishes).
4. **WhatsApp Business Cloud API** — a Meta app with the WhatsApp product
   added: phone number ID, access token, app secret (for webhook signature
   verification), and a verify token you choose yourself for the webhook
   handshake.
5. **Isiolo ward boundary GeoJSON** — for the one-ward validation gate, I
   need to know which ward to validate on, and either a boundary file from
   you or the go-ahead to fetch/build one (see `config/wards/README.md` for
   sourcing options — likely the IEBC/HDX Kenya admin boundaries dataset).
6. **WPDx API token** (optional) — the public WPDx endpoint works
   unauthenticated for reasonable volumes, but a token avoids rate-limiting
   on the full-county run.

Drop credentials into a `.env` (copy `.env.example`) — nothing in this repo
reads secrets from anywhere else.

## Known gaps / things to flag before day 1

- **Borana translations in `app/services/i18n.py` are a draft**, not
  reviewed by a native speaker. This is a real risk (wrong grazing/water
  advice delivered confidently in the wrong words) — get a native Borana
  speaker, ideally from Isiolo, to check every string in
  `BORANA_TEMPLATES`/`CONDITION_TEXT_BO`/`WATER_TEXT_BO` before the one-ward
  validation gate. Swahili strings are more reliable but still worth a
  native check on the domain-specific vocabulary.
- **No ward boundary files are bundled** — the import and zone-generation
  scripts are ready but need one to actually run. Tell me the validation
  ward and I'll fetch/build its boundary next.
- **Map image generation** (mentioned in the spec's delivery channel) isn't
  built yet — the WhatsApp handler currently sends text only. Straightforward
  to add once the core loop is validated (render the species zone + water
  point onto a static map, upload to R2, send via `whatsapp_client.send_image_bytes_url`).
- **Species-ring radii and advisory thresholds are unvalidated defaults** —
  both live in `config/*.yaml` specifically so they can be tuned from
  ground-truth feedback without a redeploy, per the spec.

## Running locally (once .env is filled in)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload

# one-ward validation (repeat in order):
python scripts/import_water_sources.py --boundary config/wards/<ward>.geojson --ward "<Ward>" --source both
python scripts/generate_piosphere_zones.py --ward "<Ward>"
python scripts/gee_compute_export.py --ward "<Ward>"

# test the read path directly:
curl -X POST localhost:8000/advisory -H 'Content-Type: application/json' \
  -d '{"lat": 0.35, "lon": 37.58, "species": "camel", "language": "swahili"}'
```
