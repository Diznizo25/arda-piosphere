# Arda Link — Piosphere Grazing Advisory

Satellite-driven grazing and water advisory for pastoralists in Kenya's arid
and semi-arid lands, delivered over WhatsApp in **Swahili** and **English**.
Phase 1 covers **Isiolo county**, Kenya.

A herder shares their location and tells us their animals (cattle, shoat, or
camel). Arda Link replies with:

- **Where the nearest usable water is** — distance, direction (in the words
  herders use: *Kaskazini, Kusini, Mashariki, Magharibi*), and how it relates
  to the water source's ward.
- **What the grazing is like right now** around that water — from the latest
  satellite data, classified into *growing grass / standing dry forage / bare
  ground*, plus a Vegetation Condition Index and the percent of usable pasture.
- **A pastoralist-first map** (PNG) centered on the herder: species rings scoped
  to how far *their* animals can travel, a "Wewe hapa / You are here" pin, the
  water pin with distance, a satellite pasture layer, and a green arrow to the
  **nearest walkable patch of good pasture** with a big readable direction +
  distance banner in Swahili.
- **Onboarding**, a **services menu**, **water-point validation** (herders can
  PIN new water points that are checked for duplicates/type before a satellite
  build is triggered), and **heart-girth weight / herd estimation** tools.

Everything satellite-related is **precomputed on a schedule** via Google Earth
Engine and stored as Cloud-Optimized GeoTIFFs in object storage (Cloudflare
R2). The live WhatsApp-facing backend never calls Earth Engine directly — it
only does fast windowed reads against precomputed data, so replies come back in
seconds.

## Why this exists

Most satellite vegetation monitoring (NDVI and similar "greenness" indices)
misreads dry rangeland as bare, dead land. In Kenya's ASALs the forage that
matters most of the year is standing *cured, dry* grass — not green vegetation —
and NDVI-only tools tell a herder that good dry forage looks the same as dirt.

Arda Link corrects for this by computing **SATVI** (a soil-adjusted index that
reads both green and dry/senescent vegetation) alongside NDVI, plus **BSI**
(bare-soil index, to catch true bare ground) and **VCI** (vegetation condition
relative to long-term normal). The advisory can therefore tell "dry forage
available" apart from "actually bare ground." See
`app/services/advisory_logic.py` and `app/services/gee_indices.py`.

Water reach is also split by species: cattle, shoats, and camels can travel very
different distances from a water point, so the same water source can be
"reachable" for a camel herder and unreachable for a cattle herder standing in
the same spot (configurable in `config/species_rings.yaml`).

## How it works

```
map water sources → buffer species-specific reach rings → compute satellite
indices inside those rings on a schedule (GEE → COG → R2) → deliver over
WhatsApp → capture herder feedback + PIN new water points → rebuild rings
```

## Stack

- **Backend:** Python 3.11 / FastAPI, deployed on Render (auto-deploy on push)
- **Relational + spatial data:** Postgres/PostGIS (Supabase), RLS enabled on every table
- **Satellite compute:** Google Earth Engine, scheduled via GitHub Actions
- **Raster storage:** Cloud-Optimized GeoTIFFs on Cloudflare R2 (S3-compatible)
- **Delivery:** WhatsApp Business Cloud API (media, buttons, voice notes)
- **CI/CD:** GitHub Actions workflows (water-point builds, two-weekly index refresh, keep-alive)

## The WhatsApp experience

Onboarding walks a herder through **strict name validation**, **preferred
language** (Swahili/English), **primary species**, and **mixed-herd
composition** — then asks them to **confirm which water point their animals
drink from**: the system presents the nearest **named** water points as a
numbered list (local name · type · distance · compass direction) + a numbered
map + a WhatsApp interactive list, remembers the choice
(`pastoralists.water_source_id`), and every map afterwards highlights **their**
water point. Names come from OSM/WPDx at import time, are backfilled for
existing points, and herders **name their own water point** when they PIN it
("Oldonyiro borehole", "Ewaso river", ...) — a pastoralist identifies a water
point by its local name, not a ward. If their water point isn't in the list,
they're guided to **PIN** it (validated, then auto-built).
After that, the message flow handles:

| Trigger | What happens |
| --- | --- |
| 📍 Location | Advisory: nearest reachable water + forage conditions + VCI |
| `map` | Pasture map (PNG) centered on the herder, rings, water pin, best-pasture arrow |
| `maji` / water | Water-source info and reach |
| `weight` | Heart-girth → weight estimate (FAO/Schwartz-Dioli equations), confidence range |
| `mifugo` / herd | Herd-size estimation from weights + composition |
| `pin` | Water-point registration: validates duplicates/nearby/type, then auto-builds |
| `status` | Water-point build status / last-computed index freshness |
| `mvua` / `rain` | Rain & drought outlook: dry-spell length, rain vs the local normal, 16-day forecast |
| `services` / menu | Quick-reply services menu (with flow escape hatches) |

Water status is captured from the herder, because the satellite knows a point
*exists* but not whether it *works today*. Every advisory ends with a one-tap
question (`1 maji yapo · 2 imekauka · 3 haipo tena · 4 pampu imeharibika · 5 maji
ya vipindi`); a bare digit is read as a status report **only when we just asked**
(menu numbers still work otherwise). Reports are stored on the water point and
suppress guidance to points reported dry/broken/gone for 60 days, then return with
a re-check nudge. Points nobody has confirmed are labelled *haijathibitishwa* in
the advisory and grey on the map, instead of implying the water is fine.

Every guided flow has escape hatches (`cancel`, `menu`, `start`), and
`conversation_state` keeps multi-step flows resumable. The water-point
confirmation can be re-shown anytime (`orodha`/`list` re-sends a fresh list +
numbered map), `hakuna`/`none` clears it and guides to PIN registration, and
flow states older than 24h **self-heal** (a stale list never traps the herder —
sending any message re-asks with fresh options).

## The satellite pipeline

1. **Import water sources** (`scripts/import_water_sources.py`) — WPDx + OSM,
   ward/county-parameterized, with PostGIS zones from
   `scripts/generate_piosphere_zones.py`.
2. **Compute indices in Earth Engine** (`scripts/gee_export_to_asset.py`) —
   NDVI, NDRE, SATVI, BSI, NDMI, NDWI, VCI, and GSW monthly recurrence are
   exported to **GEE Assets** (the free path; the old Drive-export path had no
   service-account quota), transferred to R2 as COGs
   (`scripts/transfer_assets_to_r2.py`), with a tiny 8x block-averaged
   overview COG for fast reads (`scripts/build_overview_cogs.py`).
3. **Refresh on a schedule** (`.github/workflows/refresh-indices.yml`,
   `.github/workflows/build-water-points.yml`) — every stage retries; a final
   R2 COG gate makes the workflow *always* exit 0 (failed builds are re-claimed
   and retried by the next run). Reads fall back from the overview to a
   decimated read of the full COG (`app/services/raster_read.py`).
4. **Keep a dated copy of every snapshot.** The canonical key is
   `cogs/<id>/indices.tif` (no date), so each refresh used to overwrite the past —
   which made any trend/onset/recovery analysis impossible to build *or validate*.
   `transfer_assets_to_r2.py` now also copies each snapshot to
   `cogs/<id>/archive/indices_<date>.tif` (a server-side R2 copy, no re-upload)
   and records the date in `water_sources.indices_as_of`.

## The rain outlook (prediction, honestly labelled)

Sentinel-2 alone cannot answer *"is it going to rain?"*, and optical indices carry
no memory of the past. Two additions make a real rain outlook possible without
pretending to more skill than we have:

1. **A 30-year climatology** — `scripts/build_rain_climatology.py` computes the
   normal monthly rainfall (CHIRPS daily, ~5.5 km, 1991-2020, mean + p10/p90) per
   water point straight from Earth Engine (12 reduce calls per point) and stores it
   in `rainfall_climatology`. Nothing at request time touches GEE.
2. **A cached series + forecast** — `scripts/refresh_environment.py` runs twice a
   day (`.github/workflows/refresh-environment.yml`), pulling the last 90 days of
   observed rain/soil moisture and the 16-day forecast per point (Open-Meteo;
   free/keyless on the non-commercial tier) into `environment_daily` and
   `environment_forecast`.

`app/services/forecast.py` is pure logic (unit-tested, no DB/network) and it is
where the honesty lives:

- **Dry is always relative to that place and month.** September at Lengwenyi
  normally brings ~1.7 mm, so "0.1 mm in 30 days" there is the *normal dry season*,
  not a drought. A deficit measured against a negligible normal (< 10 mm/30 days)
  is reported as `dry_season`, never as drought.
- **Forecasts are labelled, never asserted.** Anything beyond ~7 days is called
  *makadirio* (an estimate), the onset date is "rain may start in about N days",
  and no bare date is ever promised.
- **Stale forecasts are dropped**, not narrated: a cached forecast older than its
  own horizon is ignored rather than telling a herder about rain predicted three
  weeks ago.
- **Fail-open**: no stored data means no rain line at all, and the herder is told
  plainly in the `mvua` service.

The advisory gains one short line (`Mvua: Siku 30 bila mvua ya maana. Mvua ni
kidogo, lakini huu ni msimu wa kiangazi wa kawaida. Utabiri (siku 15): hakuna mvua
inayotarajiwa (makadirio).`) and the `mvua` service (menu 8) gives the fuller
answer with an action attached.

## The conversational layer (a grounded assistant, not a free chatbot)

A herder can just type a question. `app/services/chat.py` answers it — and the
design constraint is that it may **never** invent a fact:

```
question → which fact sections? (keyword routing, no model)
         → facts from OUR data (advisory / rain outlook) + curated entries
           (config/pastoral_knowledge.yaml)
         → ONE model call (app/services/ai.grounded_answer) to phrase it
         → HARD VALIDATION: every number traceable to the facts, no URLs/markdown,
           right language, ≤550 chars
         → on any failure: a deterministic answer built from the same facts
```

Why validation instead of trust: this bot tells a man whether to walk 200 cattle to
water. An invented distance is not a typo, it is a lost herd. Anything the
validator cannot trace back to the facts is discarded and replaced with the plain
deterministic sentence — so the chat degrades, it never lies. Measured behaviour
from the test suite: a model reply containing an invented `240 mm` and a
wrong-language reply are both rejected and replaced at runtime.

Three more deliberate choices:

- **Disease questions never reach the model.** Symptom or death keywords route to a
  fixed reply whose only advice is: isolate the animal, do NOT take a sick animal to
  a public water point (that is how disease spreads between herds), call the ward
  livestock officer. A plausible-sounding diagnosis can kill a herd, and no
  environmental dataset can predict an outbreak.
- **The knowledge base is curated and sourced.** Every entry in
  `config/pastoral_knowledge.yaml` must carry a `source`, and entries marked
  `needs_review: true` are delivered **with that marker shown to the herder** —
  never dressed up as settled fact. Only a qualified person (county livestock
  officer, vet, ILRI/extension) flips that flag.
- **Cost is bounded, latency is honest.** One call per question with a per-herder
  daily cap (`DAILY_LLM_LIMIT`); over the cap the herder still gets the
  deterministic answer. Reasoning models bill hidden thinking tokens (measured:
  256-400 per short exchange), which is why the token budget must stay generous —
  a small `max_completion_tokens` gets fully consumed by the reasoning pass and
  produces an *empty* reply.

Test it in production without sending a WhatsApp message:

```bash
curl -X POST -H "X-Debug-Key: <WHATSAPP_VERIFY_TOKEN>" -H "Content-Type: application/json" \
  -d '{"text":"mvua itanyesha lini?","lat":0.5669,"lon":37.2402}' \
  https://arda-piosphere.onrender.com/dev/chat
```

The response says whether the model was used (`used_llm: false` means the
deterministic path answered) and whether the disease guardrail fired.





## The map renderer

`app/services/map_renderer.py` renders a herder-friendly 1024×1024 PNG in pure
PIL + stdlib math (Web Mercator is closed-form; no projection library):

- OSM raster base tiles (public server, real User-Agent, short timeout, beige
  fallback so a map *always* renders).
- Species rings drawn outer→inner from PostGIS, zoomed so the herder's species
  ring fits.
- **Centered on the herder**, with blue "Wewe hapa" / red water pins, a line and
  distance badge between them, and the ward direction.
- Satellite pasture overlay: **green = grass, brown = dry forage, red = bare**
  (areas the satellite can't read reliably are left uncoloured), with
  "% usable pasture" in the legend.
- **Rivers are mapped as water** (`config/rivers.geojson`, 180 OSM segments):
  a river is a LONG water body, so its grazing zones are distance **ribbons
  along the river line** (comfortable ≤0.5 R, edge ≤0.8 R, far limit = R)
  rather than rings around one point — see `app/services/river_zones.py`.
  Point sources (wells/boreholes/pans) keep circular piosphere rings.
- **Green arrow to the nearest walkable good patch** (a ~2 km cluster around
  the closest good pixel — not a far-away global centroid) and a big
  bottom-center banner: `Malisho bora: Kaskazini-Mashariki · 3.2 km`.
- Big **bold fonts** (DejaVu/Arial fallback), a large place-name banner
  (ward · county), **landmark labels** (towns/villages/rivers/markets from a
  committed OSM gazetteer), nearby water sources as **type-coloured markers**
  (blue=river, orange=borehole, teal=well, green=spring, cyan=pan) with local
  names — or "Kisima karibu na <village>" for unnamed points — scale bar and a
  clear north arrow — readable on a phone after WhatsApp downscaling.
- **Numbered water-point markers** (1..N) match the confirmation choice list;
  the herder's **confirmed water point** gets a distinct "Maji yako" pin.
- **Confirmation "options" map** (`fit=1`): zooms out so the herder AND every
  numbered water point fit on screen (no rings) — a herder whose nearest
  registered points are far away still SEES them, instead of empty land.
- **Interactive Google-Maps-style live map** (`GET /mapview/?lat=..&lon=..&...`):
  a mobile, zoomable Leaflet page (OpenStreetMap) with the herder pin, all
  nearby water points (type-coloured + numbered), and the piosphere rings.
  The link is sent inside WhatsApp captions ("tap to open & zoom").
- **Never blank:** when satellite data isn't built yet, the map shows a clear
  amber "pasture data being prepared" notice + loading hatch instead of nothing.
- `GET /map/{water_source_id}.png?lat=..&lon=..&species=..&pasture=1&lang=swa&confirm=..&numbered=..&fit=0&v=..`
- `GET /mapview/?lat=..&lon=..&species=..&lang=swa&numbered=..` (public, HTML)

## Project layout

```
app/
  main.py                     # FastAPI app
  config.py                   # settings (whitespace-stripped env parsing) + config loaders
  db.py                       # Postgres/PostGIS connection (RLS setup, connect_timeout)
  models/schemas.py           # request/response models
  routers/
    whatsapp.py               # webhook: onboarding, advisory, map, PIN, weight, services
    maps.py                   # /map/{id}.png rendering endpoint (cached)
    advisory.py               # advisory endpoint
    ground_truth.py           # herder feedback capture
    water_sources.py, dev.py, run.py, legal.py
  services/
    advisory_logic.py         # SATVI/BSI-aware forage classification
    advisory_service.py       # ties the read path together
    raster_read.py            # COG overview reads + per-species-zone stats
    map_renderer.py           # pastoralist-first map PNG renderer
    water_reach.py            # species-scoped nearest-reachable-water query
    water_validation.py       # PIN validation (duplicate / nearby / type confirm)
    build_tracker.py          # water-point build state machine (requested → built)
    registration.py           # onboarding + mixed-herd composition
    conversation.py           # multi-step flow state
    weight.py                 # heart-girth weight + herd estimation
    pastoralists.py           # herder records + last-location tracking
    water_sources.py          # water-source + zones queries
    speech.py, ai.py          # voice-note transcription, optional AI replies
    i18n.py                   # Swahili/English message templates
    whatsapp_client.py        # WhatsApp send helpers (text/buttons/media)
    storage.py, gee_auth.py, gee_indices.py, ground_truth.py, build_progress.py
migrations/                   # 001_init_schema, 002_water_point_builds, 003_personalization_weight
config/                       # species_rings.yaml, advisory_thresholds.yaml, weight_formulas.yaml, wards/
scripts/                      # ops + validation tooling (see below)
.github/workflows/            # build-water-points, refresh-indices, keep-alive
```

## Database

Core tables: `water_sources`, `piosphere_zones` (species rings), `pastoralists`
(+ `full_name`, `herd_composition`, `onboarded_at`, `water_source_id` — the
herder's confirmed water point), `ground_truth_reports`, `water_point_builds`,
`conversation_state`, `weight_records`, `herd_estimates`, `query_log`.

Water status (migration 009): `water_sources.status` / `status_updated_at` /
`status_source` / `status_reports` — herder-reported water state (one-tap digits),
which suppresses guidance to points reported dry/broken/gone for 60 days.

Environment (migration 010): `rainfall_climatology` (monthly CHIRPS normals per
water point), `environment_daily` (observed rain + soil moisture),
`environment_forecast` (cached 16-day forecast), `water_sources.indices_as_of`
(when the satellite snapshot was taken).

Migrations are applied with `python scripts/apply_migration.py migrations/<file>.sql`
(`--check <tag>` verifies a migration without applying anything).

Row-level security is enabled on all tables.

## Ops tooling (`scripts/`)

Pipeline:
- `gee_export_to_asset.py` — Earth Engine → GEE Asset export (free path)
- `transfer_assets_to_r2.py` — assets → R2 COGs (+ overview + dated archive), self-healing
- `build_overview_cogs.py`, `build_water_point.py`, `pin_water_point.py`
- `refresh_indices.py` — full refresh for a ward/county
- `build_rain_climatology.py` — 30-year CHIRPS monthly normals per water point
- `refresh_environment.py` — observed rain/soil moisture + 16-day forecast (2x daily)
- `apply_migration.py` — apply/verify a migration SQL file

Checks & debugging:
- `check_r2_state.py` — verify every asset is in R2 with the right size
- `validate_cog_data.py` — per-band stats + scientific plausibility
- `check_build_progress.py`, `fetch_run_log.py`, `check_render_deploys.py`
- `check_prod_map.py`, `verify_map_center.py`, `verify_prod_map_geo.py` —
  verify the live map is geolocated correctly
- `test_pasture_render.py` (mocked, no network), `test_weight_service.py`,
  `test_rain_outlook.py` (rain-outlook logic + wiring), `test_chat_layer.py`
  (chat routing, number/disease guardrails, fail-open), `test_conversation_flows.py`
  (end-to-end WhatsApp flows)

Deployment & scheduling:
- `trigger_render_deploy.py`, `trigger_build_workflow.py`,
  `cancel_workflow_run.py`, `set_render_env*.py`, `transfer_watchdog.py`

## Live ops dashboard

`/dashboard` (protected by `DASHBOARD_TOKEN`, pass `?key=<token>`) gives a live
view of the whole system:

- **Health chips** — DB + R2 reachability, deploy commit, uptime, last build &
  last query timestamps.
- **KPI cards** — water points, COGs built, herders, queries in 24h (+ avg
  latency, errors), builds by status, feedback, weights, active flows.
- **Live water-point map** (Leaflet) — every registered water point colored by
  build status (built/running/pending/failed/seed), with popups and a toggle
  that draws the species rings.
- **Charts** — queries per day by kind, water points by source, builds by
  status, herder feedback.
- **Recent activity** — advisory/map queries, build events, ground-truth
  reports (auto-refreshes every 30s).
- **COG explorer** — per water point: all 8 index-band stats (mean/min/max/std)
  and color-mapped preview images read straight from R2.

Query/activity data lives in `query_log` (migration 004); the advisory and map
paths write to it fail-open so logging never breaks the herder experience.

## Deployment

- **Render** auto-deploys `main` on push; the FastAPI app runs Uvicorn.
- **GitHub Actions** runs the satellite compute + COG build pipeline on
  schedule and on demand. Both workflows pin Python **3.11** (rasterio/osmium
  have no cp312 wheels), retry every stage, and never exit non-zero on a failed
  build (they re-claim/retry the work instead). The two-week `refresh-indices`
  refresh uses the **free GEE-Asset export path** (`gee_export_to_asset.py
  --export-only --force` — the service account has no Drive/GCS storage quota,
  so the old Drive export always failed), scopes the R2 transfer to the ward,
  and reports its outcome to the dashboard activity feed.
- Water-point builds are tracked in `water_point_builds` (requested → building
  → built) and can be checked over WhatsApp with `status`.

## Status & roadmap

**Live:** onboarding, advisories (SATVI/NDVI/BSI/VCI), species rings, pasture
maps with direction guidance, water-point PIN validation + auto-build, weight &
herd tools, services menu, voice-note replies, ground-truth capture, herder
water-status reports that gate guidance, dated COG history, the rain/drought
outlook (`mvua`) built on a 30-year CHIRPS climatology + a cached 16-day forecast,
and a grounded conversational layer (`/dev/chat` to probe it).

**Known open items:**
- Swahili message templates should be reviewed by native speakers before wide
  field rollout.
- Species ring radii and forage thresholds (`config/*.yaml`) are starting
  defaults, meant to be tuned from real ground-truth feedback.
- Open-Meteo's free tier is non-commercial (10k calls/day): licence the customer
  endpoint or self-host before charging for the service.
- Rain-outlook thresholds (`app/services/forecast.py`: wet day, onset window,
  meaningful normal) are reasoned defaults, not yet validated against herder
  observations — the same ground-truth loop will tune them.
- Forage trend analysis becomes possible only once a few dated COG archives exist;
  drought onset, green-up timing and recovery should be built on that series.
- Vet/disease-risk *hazard windows* (vector flush after sustained rain, crowding at
  shrinking water) need a county veterinary partnership before we promise anything
  about disease; environment alone cannot predict outbreaks.
- Market signaling, peer connection, vet registry, and marketplace are later phases.

## Getting started

```bash
cp .env.example .env   # fill in Supabase, R2, Earth Engine, WhatsApp credentials
pip install -r requirements.txt

# apply the schema
psql "$DATABASE_URL" -f migrations/001_init_schema.sql
psql "$DATABASE_URL" -f migrations/002_add_water_point_builds.sql
psql "$DATABASE_URL" -f migrations/003_personalization_weight.sql

uvicorn app.main:app --reload
```

One-ward validation pipeline:

```bash
python scripts/import_water_sources.py --boundary config/wards/<ward>.geojson --ward "<Ward>" --source both
python scripts/generate_piosphere_zones.py --ward "<Ward>"
python scripts/gee_export_to_asset.py --ward "<Ward>"
python scripts/transfer_assets_to_r2.py --ward "<Ward>"
```

Test the advisory endpoint directly:

```bash
curl -X POST localhost:8000/advisory -H 'Content-Type: application/json' \
  -d '{"lat": 0.35, "lon": 37.58, "species": "camel", "language": "swahili"}'
```

