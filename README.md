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

## The water loop (one tap, then everyone on that point)

Migration 009 let a herder report a water point's status. Migration 011 makes that
tap **worth more than one person's answer**:

- **Fan-out** — when a status lands, the other herders who drink from the *same*
  point are told what a herder confirmed, anonymously, and never more than once per
  12 hours (`water_notices` is both the log and the cooldown). One tap, ten people
  informed.
- **Queue** — straight after a status report the herder is asked one more digit:
  `1` short line, `2` long line. A full tank with 40 herds queued is not the same
  decision as a full tank with nobody there. Readings older than 24 hours are not
  shown (`water_loop.QUEUE_FRESH_HOURS`).
- **Repair closure** — `imekarabatiwa` (or "repaired", "maji yamerudi") sets the
  point back to functional *immediately* and tells everyone we had warned. Without
  it, a pump fixed the day after a report would stay suppressed for the whole
  60-day stale window, and herders would walk past working water because our record
  said it was dead.

Rules that keep it a network rather than a broadcast channel: the fan-out fires only
on a *change* of status, never for `unknown`, is cooldown-guarded, and is backgrounded
so a reporter's acknowledgement is never delayed by it. Everything is fail-open.

## Pest and parasite windows (check first, never diagnose)

The safest prediction this system can make, and the one that targets lost weight and
milk rather than an outbreak: the conditions that favour ticks, stomach worms, flies
on wounds and wet-ground hoof problems. `app/services/pests.py` computes four small,
transparent scorecards from data we already store — rain (7 and 14 day), soil
moisture, temperature, humidity and (optionally) NDMI from the satellite COG:

| Window | Gate (moisture) | Signals | Where we tell the herder to look |
|---|---|---|---|
| Ticks | rain 7d ≥ 10 mm, damp soil or moist vegetation | rain, soil, temperature band, NDMI | ears, under the tail, udder, between the legs, neck |
| Worms | rain 14d ≥ 20 mm or damp soil | rain, days since last rain, warmth, soil | eyelid colour, gums, spine, belly shape |
| Flies / wounds | rain 7d ≥ 5 mm or humidity ≥ 60% | rain, warmth, humidity | any wound, navel of young stock |
| Hooves | ≥ 3 wet days in a row or very wet ground | wet-day streak, soil | all four hooves, the standing area at the water point |

Why it is safe to ship: the herder's own eyes confirm or dismiss the window, so being
wrong costs him five minutes — never a diagnosis, never a drug bill, never a
contradiction of the county vet. Three mechanical guarantees, asserted in
`scripts/test_pests.py`:

- **heat alone never fires a window** (in the ASALs warmth is the background state,
  so every window has a moisture gate),
- **one available signal can never raise a window past `watch`** (a false alarm costs
  a household real money),
- **nothing we render names a drug, prescribes a treatment or diagnoses** — the
  forbidden-word list is a test, not a comment. If the herder finds something, the
  message routes him to the vet or agrovet.

Tiers are `quiet → watch → rising → high`. Two wording decisions that came out of
review, both now enforced by tests:

- **The service is named KUPE NA MINYOO (ticks and worms), not "wadudu".** "Wadudu" is
  the generic word for bugs — grain weevils and house insects — and would not tell a
  pastoralist that this is about his animals. It survives only as a keyword alias.
  Menu item 10 is `🔍 KUPE NA MINYOO — angalia mifugo yako`.
- **A quiet answer gives the reason, never a verdict.** "Conditions are normal" tells
  a herder who just spent a message asking about ticks nothing at all. The quiet reply
  now names why the windows are down (*"Hakuna mvua hata moja katika siku 30 zilizopita,
  na udongo ni mkavu"*) and what will change it (*"Kupe na minyoo huongezeka mvua ya
  mfululizo ianzapo. Tutakutumia tahadhari wakati huo."*). With no data at all it says
  so plainly instead of implying the coast is clear.

The wording and the checklist come from
`config/pest_guidance.yaml` (marked `needs_review`: a county vet must check it before
the first message reaches a herder). Answers are stored in `pest_observations`
(migration 012) with `seen` true *or* false — a "I looked and saw nothing" reply is as
valuable as a positive one, because it is what keeps a window honest. Probe it with
`POST /dev/pest` (real data by `water_source_id`, or synthetic by `rain: [...]`).

## The weekly note (a rhythm, not a reply)

Retention is an appointment. `scripts/send_weekly_note.py` builds and sends the same
shape of message every Monday: the rain line, where it rained by place name, the
herder's water point and its queue, the pest check when a window is up, a live-map
link, and exactly one one-tap question. Three things it refuses to fake:

- **Dry run by default** (nothing is sent without `--send`), so a typo cannot message
  hundreds of phones.
- **WhatsApp's 24-hour window is respected** — a free-form business message is only
  allowed within 24 hours of the herder's own last message, so herders outside it are
  recorded as `outside_24h_window` in `weekly_notes` (migration 013) instead of being
  messaged into the void. Reaching that group needs one approved utility template.
- **One note per herder per week** (unique key on `weekly_notes`), so a retry, a
  manual run and the scheduled run cannot triple-message anyone.

The sender also records the note as the herder's last advisory point, so his reply
`2` (imekauka) lands on the right water point instead of being ignored.

**Where the rain fell is a sentence, not a shaded band.** The map stays as it is and
instead gains a stamp of its own age (`malisho: picha ya 05 Sep - kadirio`). The
reasoning: our rain data is a value *at a point*, so a band would claim spatial
precision we do not have, look authoritative when wrong, and be staler than the words
(the text rides the twice-daily refresh). Pastoralists also speak in places —
*"mvua ilinyesha upande wa Kipsing"* — so `forecast.place_rain_line()` compares the
herder's 7-day total with the nearest named points and says nothing at all when the
difference is not a movement decision.

The satellite refresh moved from fortnightly to **weekly** (`0 3 * * 1`) for the same
reason: an alert that fires on 7-day rain signals should not point at a 14-day-old
picture. If GEE export or Actions minutes ever bite, split it into two alternating
half-batches (same total work, still weekly per point).

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

The chat layer remembers the conversation: the last few turns plus the herder's last
known place live in `conversation_state` (state `chat.memory`, 6-hour expiry), so a
follow-up ("na nihamie wapi?") is answered from where they last said they were
without asking again. The recap is passed to the model explicitly labelled as
**not** a new source of facts, and the number-validation allow-list still comes from
the facts bundle alone — memory can add context, never evidence.

## Saying where you are in words (landmark intake)

Most herders will never send a WhatsApp location pin. They type what they say out
loud, so `app/services/landmarks.py` resolves a named place to coordinates:

```
"niko karibu na Kipsing"     → matched    (Kipsing, village, 0.979, 37.324)
"niko karibu na Kipsin"      → matched    (typo tolerated, score 0.94)
"I am at Wamba market"       → matched
"nipo Lengwenyi"             → matched    (a water point, not in the gazetteer)
"niko karibu na Ewaso Nyiro" → ambiguous  (4 places share the name)
"niko karibu na Oldonyo Sabor" → not found (we say so, and ask for a location)
"habari yako"                → none       (no false positives on chatter)
```

Examples in the docs are names from *our* gazetteer on purpose: "Oldonyo Sabor" is a
real place in Isiolo but not (yet) in `config/landmarks.geojson`, and shipping an
example the system cannot resolve is worse than shipping none.

It searches **two** name spaces, because a herder's landmarks are a mix of both:
the gazetteer (`config/landmarks.geojson`, 384 towns/villages/hamlets/markets/
rivers/peaks built by `scripts/build_landmarks.py`) and **our own named water
points** (`water_sources.name`) — "Lengwenyi well" is a landmark to the herder
standing next to it even though it is not in the gazetteer.

Design rules the tests enforce:

- **Spelling tolerance, not guessing.** difflib + token-subset matching handles
  "Kipsin" for "Kipsing" and a place name inside a sentence. Ordinary chatter scores
  low and resolves to nothing.
- **A name we don't know is admitted, never guessed.** "niko karibu na Oldonyo Sabor"
  (not in our gazetteer) replies *"Samahani, sijui eneo hilo. Tuma eneo lako
  (location)..."* rather than showing a menu or inventing a near-match. Detection is
  conservative (must say where, be short, and mention nothing the other services own)
  so it can never hijack "niko na ng'ombe 40".
- **Ambiguity is reported, never resolved by luck.** Duplicate names are the NORMAL
  case — 194 names in our own gazetteer repeat in places >5 km apart, because a
  river's name follows its whole course. If we know roughly where the herder is
  (their last pin, their water point) the nearest matching place wins; otherwise we
  ask. Two names within 5 km are the same physical place (a village and the market
  inside it), so they are not treated as ambiguous. Picking one silently would send a
  herd to the wrong side of the county.
- **An unhelpful list becomes a request.** Four identical "Ewaso Nyiro (mto)" options
  distinguish nothing, so that case replies "that name appears in several places —
  send your location" instead of pretending to offer a choice.
- **One delivery path.** A named landmark goes through exactly the same
  `_deliver_location_info()` as a WhatsApp location pin: water reach, pasture
  condition, the rain outlook, the position on the map, and the one-tap water
  question — so the two can never drift apart (there is a test for that).
- **Ordering matters and is tested.** Landmark intake runs *after* the guided flows
  (during the PIN flow the herder is typing a place name for a *new* water point —
  hijacking that would break registration) and *before* the service keywords, so
  "maji yapo wapi?" asked after naming a place means "here".

Probe it in production without WhatsApp:

```bash
# resolve a place name on its own
curl -X POST -H "X-Debug-Key: <WHATSAPP_VERIFY_TOKEN>" -H "Content-Type: application/json" \
  -d '{"text":"niko karibu na Kipsing"}' \
  https://arda-piosphere.onrender.com/dev/landmark

# or run the whole chain the way WhatsApp does it: resolve the name first, then
# answer from the resolved place (add "phone" to reuse one herder's memory)
curl -X POST -H "X-Debug-Key: <WHATSAPP_VERIFY_TOKEN>" -H "Content-Type: application/json" \
  -d '{"text":"nipo Lengwenyi","resolve_landmark":true}' \
  https://arda-piosphere.onrender.com/dev/chat
```

`resolve_landmark: true` matters: without it the probe would report a bare place name
as an unanswerable question, because the resolution happens in the WhatsApp handler
*before* the chat layer — a mistake this flag exists to prevent.

## Data freshness (and how to see it)

Trust depends on the data being current, so freshness is a first-class dashboard
panel rather than something you check by hand (`/dashboard` → **Data freshness**,
`/dashboard/api/freshness` for JSON). Each source is compared with the cadence it is
supposed to have, and classified `fresh` → `aging` → `stale` → `missing`:

| Source | Expected every | Where the age comes from |
|---|---|---|
| Satellite pasture (COG) | 14 days | **R2 object upload time** — the file the advisory actually reads |
| Rain observed | 12 h | `max(environment_daily.observed_on)` |
| Rain forecast | 12 h | `max(environment_forecast.generated_at)` |
| Rain climatology | 365 days | `max(rainfall_climatology.updated_at)` |
| Herder reports | 7 days | `ground_truth_reports` + water status freshness |

It also lists which scheduled pipelines last reported in (they log to `query_log`),
so a job that silently stops shows up as a stale row instead of a surprise later.

At the time of writing, measured: rain observed **13.6 h**, forecast **4.3 h**,
climatology **6.1 days**, satellite COGs **9.1 days** (inside the 14-day cadence, no
dated archive yet), and **0 herder reports** — the one row that is red, and a product
signal rather than a pipeline fault: nobody has answered the one-tap water question
yet.

`scripts/backfill_indices_as_of.py` repairs the one field that had drifted: the COG
upload time is authoritative, so it writes it into
`water_sources.indices_as_of` (it is otherwise only set by the transfer pipeline, so
it was NULL for every point until the next refresh ran).



Herder feedback was blunt: advisories and voice notes were hard to follow — odd
punctuation, stilted wording, and one question that made no sense at all ("Majina
hayo ni ya kweli?" — *are those names real?* — asked after an advisory about water,
copy-pasted from the water-point confirm flow). Investigating the real strings and the
TTS path found five separate causes, all now fixed and enforced by
`scripts/test_message_style.py`:

1. **The model "rephrase" was destroying structure.** A 5-line advisory came back as
   one 309-character line with six colons. A rephrase is now sent ONLY if every
   number survives, there are no semicolons, no line has two colons, no lines were
   merged and the distance is intact — otherwise the deterministic text goes out.
   `ADVISORY_REPHRASE_ENABLED=false` turns it off entirely without a deploy.
2. **The voice note read artifacts.** Emoji (⚠️ ☀️ 🌧 ⏳) and bullet glyphs were
   spoken aloud, newlines were flattened into one breathless stream, and parentheses
   were *deleted* — which silently removed "(makadirio)" from audio only, so the
   spoken advice lost its uncertainty while the written advice kept it. Now
   `speech_segments()` strips emoji/bullets, expands units (`4.3 km` → "4.3
   kilomita", `40%` → "asilimia 40", `30-45` → "30 hadi 45", `~7` → "takriban 7"),
   keeps every line as its own sentence, and the TTS joins them with 300 ms pauses so
   one idea does not run into the next.
3. **Wording built for a dashboard, not an ear.** Nested colons, repeated nouns
   ("Maji: uhakika wa maji", "Water: water reliability"), a grammar break
   ("Malisho karibu na maji ni nyasi kavu nzuri ya malisho ipo."), and a missing full
   stop that ran two sentences together in audio. All templates, the water-status
   prose (`status_sentence()` instead of terse labels like "imekauka") and the rain
   lines are now short sentences with units spelled out.
4. **Uncertainty is a sentence, not a bracket** — "Huu ni makadirio." survives into
   the voice note, unlike a parenthetical.
5. **The one-tap question asks about water** at the point described, and a test
   asserts it mentions water/chanzo and never "majina".

The rule for anyone editing these strings: one idea per line, short sentences, no
semicolons, at most one colon per line, spell out units, and never leave a line
without terminal punctuation. The tests check ~256 generated advisories mechanically.


```
Dockerfile              multi-stage: deps (build-essential + wheels) → slim runtime
requirements-web.txt    runtime-only deps, derived from app/ imports
docker-compose.yml      service only, or service + local PostGIS (--profile local-db)
.dockerignore           keeps .env, secrets/, scripts/ and data/ out of the image
```

```bash
docker build -t arda-link:latest .
docker run --rm -p 8000:8000 --env-file .env arda-link:latest
curl http://localhost:8000/health
docker compose --profile local-db up --build     # + PostGIS for a self-contained stack
```

Deliberate choices:

- **The image is the web service, not the pipeline.** GEE exports, landmark/water
  imports and migrations need `requirements.txt` (earthengine-api, osmium,
  geopandas) and run from the export machine / GitHub Actions. `gee_auth` therefore
  imports `ee` lazily, so the container does not carry a batch-compute dependency it
  never uses.
- **Secrets are runtime-only.** `.dockerignore` keeps `.env` and `secrets/` out of
  the build context, so the Supabase service-role key cannot end up in a layer.
- **Non-root, healthchecked.** Runs as uid 10001 and answers `/health`; it handles
  other people's phone numbers and locations, so it should not be able to write to
  its own code.






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
water point), `environment_daily` (observed rain + soil moisture + temperature and
humidity, added in 012), `environment_forecast` (cached 16-day forecast),
`water_sources.indices_as_of` (when the satellite snapshot was taken).

The water loop (migration 011): `water_sources.queue_level` / `queue_updated_at` /
`queue_reports` / `repaired_at`, `ground_truth_reports.queue_level`, and
`water_notices` (the fan-out log *and* its 12-hour cooldown).

Pest and parasite windows (migration 012): `pest_observations` — `pest_key`
(ticks/worms/flies/hooves), `seen` true or false, phone, water point, date.

The weekly note (migration 013): `pastoralists.last_inbound_at` (WhatsApp's 24-hour
window, so we know who can legally receive a free-form message) and `weekly_notes`
(one row per herder per week, with `delivered` and `skipped_reason`).

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
- `refresh_environment.py` — observed rain/soil moisture + temperature/humidity +
  16-day forecast (2x daily)
- `send_weekly_note.py` — the Monday note (dry run by default; `--send` to deliver)
- `apply_migration.py` — apply/verify a migration SQL file

Checks & debugging:
- `check_r2_state.py` — verify every asset is in R2 with the right size
- `validate_cog_data.py` — per-band stats + scientific plausibility
- `check_build_progress.py`, `fetch_run_log.py`, `check_render_deploys.py`
- `check_prod_map.py`, `verify_map_center.py`, `verify_prod_map_geo.py` —
  verify the live map is geolocated correctly
- `test_pasture_render.py` (mocked, no network), `test_weight_service.py`,
  `test_rain_outlook.py` (rain-outlook logic + place-rain wording + the map's age
  stamp), `test_chat_layer.py` (chat routing, number/disease guardrails, fail-open),
  `test_water_loop.py` (queue digits, fan-out rules, repair keywords),
  `test_pests.py` (window thresholds, the moisture gate, and the no-drug /
  no-diagnosis boundary), `test_weekly_note.py` (note shape, 24-hour window,
  once-a-week rule), `test_conversation_flows.py` (end-to-end WhatsApp flows)

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
water-status reports that gate guidance, the water loop (status fan-out, waiting
queue, repair closure), pest & parasite check windows, the weekly note, dated COG
history, the rain/drought outlook (`mvua`) built on a 30-year CHIRPS climatology +
a cached 16-day forecast, and a grounded conversational layer (`/dev/chat` to probe
it).

**Build order from here** (the reasoning is in `docs/retention_plan.md`):

1. Water loop — shipped (fan-out, queue, repair closure).
2. Pest & parasite windows — shipped (check-first; vet review of
   `config/pest_guidance.yaml` still needed before wide rollout).
3. Weekly picture fixes — shipped (map age stamp, rain by place names, weekly
   satellite refresh); fixed weekly send — shipped (`send_weekly_note.py`, needs one
   approved WhatsApp utility template to reach herders outside the 24-hour window).
4. Herd ledger with a payoff — next: household/group layer (two phones, one herd),
   cohort counts, weekly `zaidi / pungufu / hakuna` changes, the monthly scorecard
   with ward-benchmarked rates, and the "you should have 44, you counted 42" check.
5. Contribution and pride (`taarifa zako 14 zimewasaidia wachungaji 60`) — small
   tally, rides on 1-4.

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
- **When adding a field to a chat facts bundle, always encode the unit in the key
  and supply herder wording.** The validator can prove a number is traceable, but
  it cannot prove the model attached the right unit to it: a live probe turned
  "siku 30" (30 days) into "miezi 30" (30 months) while every digit checked out.
  The defence is structural — say the unit in words, translate enums at the
  boundary, and offer a `suggested_reply` — not a better prompt.
- Forage trend analysis becomes possible only once a few dated COG archives exist;
  drought onset, green-up timing and recovery should be built on that series.
- **Coverage is the limit on every answer, not the plumbing.** A herder at Kipsing
  (north Isiolo) asked for rain and got an honest "no data here" — there is no water
  point within reach to anchor the outlook to, and the environmental series is keyed
  by water point. More named water points is what turns that into an answer.
- Landmark intake searches the gazetteer (384 names) plus our named water points.
  Names herders actually use that are missing from both — e.g. "Oldonyo Sabor" — get
  the honest "sijui eneo hilo, tuma eneo lako" reply; adding them to
  `config/landmarks.geojson` (or as water points) is the fix, not better matching.
- Pest windows ship as **conditions + where to look**, never a diagnosis: heat alone
  cannot fire a window, a single available signal cannot raise one past `watch`, and
  `scripts/test_pests.py` asserts mechanically that no drug name, treatment verb or
  diagnosis can reach a herder. The pest thresholds are literature-based defaults to
  be tuned against `pest_observations`, and `config/pest_guidance.yaml` is marked
  `needs_review` until a county vet reads it.
- Disease-risk windows (vector flush after sustained rain, crowding at shrinking
  water) still need a county veterinary partnership before we promise anything about
  disease; environment alone cannot predict outbreaks.
- Reaching herders outside WhatsApp's 24-hour window needs one approved utility
  template. Until then the weekly note delivers to in-window herders only, and the
  rest are recorded as `outside_24h_window` rather than counted as reach.
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

