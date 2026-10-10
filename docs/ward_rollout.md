# Ward rollout: Oldonyiro → all of Isiolo County

*What we have, what it costs, and the order to do it in. Decision doc, not a wish list.*

## What is actually in place today

| Piece | State |
|---|---|
| **Ward boundaries** | `config/wards/kenya_adm3_wards.geojson` (1,452 ADM3 polygons, all Kenya) — **all 10 Isiolo wards matched** and split into `config/wards/<ward>.geojson` by `scripts/split_wards.py` |
| **Water points** | Imported from **OSM + WPDx** inside a ward polygon: `scripts/import_water_sources.py --boundary config/wards/<ward>.geojson --ward "<Ward>" --source both` (free, minutes) |
| **Piosphere zones** | `scripts/generate_piosphere_zones.py --ward/--county` (cheap geometry, DB only) |
| **Satellite rasters** | `scripts/gee_export_to_asset.py --ward/--county/--water-source` → `scripts/transfer_assets_to_r2.py`. **The expensive step: >1 hour per point, ~507 MB full COG + 8.2 MB overview** |
| **Refresh** | `.github/workflows/refresh-indices.yml` runs weekly, scoped to **Oldonyiro only**; `scripts/refresh_indices.py` accepts `--ward`, `--county`, `--water-source` |
| **Database** | **Oldonyiro: 9 water points, all 9 with a COG. Every other Isiolo ward: zero rows.** |
| **Herder pinning** | Built and live: `PIN` flow, build tracker, `pin_water_point.py` |

Isiolo's ten wards: Wabera · Bulla Pesa · Ngare Mara · Burat · Oldonyiro · Kinna ·
Garbatulla · Sericho · Chari · Cherab.

## The cost, honestly

Per water point the full pipeline is: import (free) → zones (seconds) → **GEE export
(>1 h, unattended)** → transfer (**~515 MB stored**: 507 MB full COG + 8.2 MB overview).

A rural ward realistically yields 20–80 OSM/WPDx points, so the county is roughly
**300–600 points**. At full depth that is:

| | Full pre-map (300–600 points) | Overview-only (8 MB/point) |
|---|---|---|
| GEE time | 300–600+ hours, unattended | same compute, smaller output |
| R2 storage | **150–300 GB** | **2.5–5 GB** |
| Time to first usable ward | 1–3 weeks of batch | hours |

**The lever we already have:** `raster_read.py` reads the **8× overview first** — "small,
fast, and the preferred read source" — and only falls back to the full COG when the
overview is missing. The advisory (zone means), the pasture map and the weekly image all
work from the overview. If nothing in the product genuinely needs full resolution, the
full COG is a 60× storage cost we are paying out of habit, and **overview-only should be
the default for new wards** until a feature proves otherwise.

## The decision: seed cheaply, compute on demand

**Neither extreme.** Pre-mapping all ten wards at full depth buys rasters around points no
herder has confirmed: 200 GB and weeks of compute for data nobody asked for. Pure
grow-with-users hands a herder in Burat a blank map and the request to "pin your water
point", which he cannot do well without names he recognises.

1. **Seed (now, cheap, no GEE):** all ten wards get their boundary in the config, their
   OSM/WPDx points imported, and their piosphere zones generated. Result: every herder in
   Isiolo can find his ward, see a **list of named points he recognises**, pick his own and
   report on it. The water loop, the pest prompt and the rain line work from there.
2. **Confirm (field, half a day per ward):** take the ward's point list to the chief, the
   committee and the agrovet — *which of these do you know, which are missing, what are
   they called?* That fixes the names (our weak spot), and it is the same visit that
   recruits the interviews. Herder-pinned points join here.
3. **Compute on demand:** rasters (overview first) are built for **points with a user** —
   confirmed, registered or reported on — through the existing `--water-source` path. Ward
   by ward, overnight, funded by demand.
4. **Refresh what is used:** the weekly workflow moves from one hard-coded ward to a list
   or a DB query ("wards with active users"), so we never pay weekly compute for a ward
   nobody opens.

**Where to start:** the ward where the ten interviews happen — otherwise we test a live
service against a blank map — then the pastoral wards by grazing overlap with Oldonyiro:
**Burat → Ngare Mara → Kinna → Garbatulla → Sericho → Chari → Cherab**, with Wabera and
Bulla Pesa last (urban, few pastoralists, but the densest phones and the easiest place to
test voice-versus-text behaviour).

## The commands, per ward

```bash
# 1. points (free, minutes)
python scripts/import_water_sources.py --boundary config/wards/burat.geojson \
    --ward "Burat" --county "Isiolo" --source both
# 2. zones (seconds)
python scripts/generate_piosphere_zones.py --ward "Burat"
# 3. rasters — the expensive step, demand-driven
python scripts/gee_export_to_asset.py --ward "Burat"          # whole ward, overnight
python scripts/gee_export_to_asset.py --water-source <id>     # one point, on demand
python scripts/transfer_assets_to_r2.py --ward "Burat"
# 4. verify what a herder in that ward would actually get
python scripts/check_r2_state.py
python scripts/validate_cog_data.py
python scripts/smoke_advisory_local.py
```

To add a ward to the weekly refresh, edit the workflow step name and its `--ward` argument
(`.github/workflows/refresh-indices.yml`); the script already accepts a ward, a county or a
single point.

## What the county gives the buyer story

The data product is a **ward-level** stream: water status, queues and pasture condition by
ward, weekly. A county, an NDMA officer or an NGO buys a ward, not a scatter of points — so
seeding all ten wards with named points and zones (even before rasters) is what makes the
stream describable, priceable and comparable across wards. It is also what lets us tell a
buyer: *"here are the wards we cover, here is the reporting rhythm, here is the coverage
gap your line would close."*

## Risks, stated plainly

| Risk | Why it matters | Mitigation |
|---|---|---|
| Full COG is 507 MB/point | 60× the storage and transfer of what the read path needs | Overview-only for new wards; full COG on request |
| OSM/WPDx names are wrong or invented | A herder shown "Burat borehole #3" loses trust instantly | The half-day confirmation visit; herder pins override imports |
| GEE export quota and runtime | >1 h per point; overnight batches can fail quietly | Export per ward overnight; verify with `check_r2_state.py` before telling anyone the ward is live |
| Weekly refresh cost grows with wards | Paying weekly compute for wards nobody opens | Refresh only wards with users (workflow input, or a DB query) |
| Urban wards pull us off the pastoral problem | Wabera/Bulla Pesa are cheap to serve and easy to demo | Keep them last, and only for the voice/text test |

## What is deliberately not decided here

Pricing a ward-month to a buyer, and whether the county or an NGO is the first buyer, are
questions for the ecosystem interviews (see `docs/field/mom_test_field_kit.md`, hypotheses
V1 and V2). This document only decides how the *coverage* grows, so that when a buyer says
yes we are not starting from nine points in one ward.
