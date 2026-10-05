# MALISHO YA LEO — the grazing ledger

*What it is:* a herder pins where the herd grazed today (one tap, a WhatsApp
location, a place name, or a tap on our map). He gets back what that grass was
worth and **what to do about the gap** — starting with the things that cost
nothing.

*Why it exists:* the two things that thin a pastoral herd are the same two things
he cannot see from where he stands — **how much forage is actually over the ridge**,
and **how much energy the walk there costs**. Everything else we have built answers
a question; this one answers the question he asks himself every morning.

---

## The two axes (never collapsed into one number)

| Axis | Question | What drives it |
|---|---|---|
| **Quantity** | Can they fill up? | standing herbaceous biomass from **SATVI** (the index this system already trusts for standing dry forage), then the **intake-rate plateau**: above ~450 kg/ha *utilisable*, the animal's appetite is the only limit; below ~120 kg/ha it cannot harvest enough, whatever the hours |
| **Quality** | Does the fill carry energy? | curing stage (NDMI) and greenness (NDVI) → metabolisable energy per kg dry matter, 9.5 MJ (green) down to 4.5 MJ (bare) |

**"Wanashiba lakini wanapungua" — they are full and still losing** — is the exact
state a herder cannot see for himself, and the reason the feature speaks in two
axes. Cured dry grass fills the rumen and delivers little; a single "pasture score"
would hide that.

## The ledger, per head per day

```
required  = maintenance x (1 + activity + heat) + locomotion
intake    = body weight x DMI% x harvest-factor x ME-density
balance   = intake - required          → surplus | holding | deficit
```

| Term | Value used | Why |
|---|---|---|
| Maintenance | 0.53 MJ ME/kg^0.75 | tropical zebu-type |
| Activity | +15% of maintenance | grazing, ruminating, postural — **separate** from walking, so the round trip is never double counted |
| Locomotion | 2.0 J/kg/m | ~2 kJ per kg per km, linear in distance |
| Heat | +8% of maintenance per 5 °C above 30 °C, cap 30% | the same walk is cheaper elsewhere |
| Harvest factor | 0.15 → 1.0 | the intake-rate plateau: on thin ground the sward, not the animal, is the limit |

**Distance alone is not energy.** A 500 kg animal walking 10 km spends roughly a
fifth of its daily upkeep on the walk; a 100 m climb costs about as much as 4 km of
flat walking, and 38 °C raises maintenance while shortening grazing time. Slope is
therefore the biggest known gap in this model — `energy.terrain_factor` is **1.0 and
documented as not computed** (a free 30 m DEM is the upgrade path), which is why the
message never pretends to be a caloric measurement.

## Offsets: free first, always

Order is the product decision, and the tests assert it:

1. **Move** — a named patch, read from the same COG, measured from the same origin,
   quantified in two parts (*better grass +X MJ, shorter walk +Y MJ*) so the herder
   can argue with the total instead of taking it on faith.
2. **Water earlier** — free, and nobody thinks of it in the heat.
3. **Salt/mineral lick** — cheap, helps digestion of dry roughage, and is **feed,
   never a medicine**.
4. **Energy supplement** — kg per head per day, **with its cost**, and a warning when
   the feed alone cannot close the gap.
5. **Sell the finished animals** — offered only when the deficit is large.

A supplement that costs more than the weight it adds is a loss, so nothing is
recommended without its cost — and **when the animals are already gaining the option
list is empty**. "Add nothing" is a required output of an honest advisor.

## Honesty rules (enforced by `scripts/test_forage.py`, not by good intentions)

* Every reading is a **BAND** with the **satellite date** printed. No date → the
  message says "tarehe haijulikani" rather than implying today.
* The band **widens where the sward is thin** (where the satellite is least
  trustworthy, ±45%) and **collapses where it is rich** (above the plateau the
  animal's appetite is the limit, ±9%) — otherwise a real surplus could never be
  reported and the ledger would push feed at a green flush.
* **"Deficit" requires the whole band to be negative.** A noisy reading never turns
  into "buy feed".
* **No drug, no dose, no diagnosis, no body-weight claim** — asserted over every
  rendered string, in both languages, the voice summary included. A mineral is
  described as a mineral; anything clinical is routed to the vet.
* **No invented totals**: without a known herd size the figures stay per head and the
  household line is omitted.
* **The quantity word and the quantity number describe the same quantity** (both
  utilisable kg/ha). A "little to eat" printed beside a standing-crop figure that
  looks like plenty is the contradiction that costs a herder's trust.
* **We remember the manyatta once** and never ask twice; the origin of the distance
  is named in the message ("kutoka manyatta yako" / "kutoka chanzo chako cha maji").
* With no satellite picture for the pin we say so (`no_data_message`) and **still
  record the walk**.

## What we cannot know (yet)

* **Browse.** Goats and camels live on shrubs, pods and acacia browse, which the
  satellite cannot see at all — our biomass is a **grass-layer** estimate and
  under-states feed where browse is doing the work.
* **Palatability and species.** Rank grass reads the same as good grass.
* **Real body weight** — so body weight is a declared class default until the
  weighing model clears its own gate.
* **Slope** (see above), and per-animal intake: figures are per *representative*
  adult animal.

## Calibration — how this stops being a set of rules of thumb

1. **The herder's own one-tap answer** ("1 mazuri / 2 kati / 3 mbaya"), stored on the
   exact event it grades, plus the existing
   `ground_truth_reports.pasture_good|pasture_poor` for the extremes. "Fair" is kept
   only on the event: forcing a middle into a good/poor bucket would corrupt the set
   we are trying to build.
2. **Quadrat clipping** — 0.25–1 m² frames, 30–60 points per ward stratified by
   SATVI, to replace the two-point transfer function in `biomass`.
3. **A scale day + body condition scoring**, to replace `energy` (the same gate the
   weight model is waiting on).
4. Then, and only then, herd-days can be quoted the way a range officer quotes them.

## The files

| File | Role |
|---|---|
| `app/services/forage.py` | the whole model: patch reading, quality, ledger, offsets, wording |
| `app/services/grazing_flow.py` | the flow: pin → answer → stored walk; manyatta registration; map tokens |
| `app/services/manyattas.py` | the registry + the `grazing_events` audit trail |
| `config/forage_energy.yaml` | every number, marked `needs_review` |
| `app/routers/grazing.py` | `POST /grazing/pin` for taps on the map page |
| `migrations/014_grazing_ledger.sql` | `manyattas`, `grazing_events`, `graze_tokens` |
| `scripts/test_forage.py` | the arithmetic, the seam between quantity and quality, the boundaries |
| `scripts/test_grazing.py` | the flow: empty states, dedupe, ask-once, two-messages-max, reachability |

## The two ways to answer, and why there are two

* **WhatsApp location pin** (one tap on the button we send) — the primary path: it
  works on any phone, needs no typing and no map.
* **Tap on the map** (`/mapview/?graze=1&t=<token>`) — for a herder who knows the
  place by sight. The link carries a **48-hour token, never a phone number**, so a
  forwarded link cannot become an answer for someone else's herd.

Both call the same flow, so a walk reported by tapping behaves identically to one
reported by pin — one service, one set of bugs.

## Probes

```
POST /dev/graze     X-Debug-Key: <WHATSAPP_VERIFY_TOKEN>
  synthetic: {"satvi":0.30,"ndvi":0.08,"bsi":0.06,"ndmi":-0.05,"walk_km":14,
              "species":"cattle","head_count":18,"temp_max_c":38,"snapshot":"05 Sep",
              "better":{"satvi":0.33,"ndvi":0.45,"walk_km":4,
                        "direction":"Kaskazini-Mashariki"}}
  real:      {"water_source_id":"<uuid>","lat":0.55,"lon":37.24}
```

Returns the bands, the per-head ledger, every offset with its cost, and the message
in both languages — so wording, economics and guardrails are reviewable without
sending anything to a herder.

## The metric that matters

Not messages sent: **grazing pins per herder per week, and whether the walk falls**.
The route answer ("Leo wapi?") is the habit engine that needs nothing from him; this
ledger is the calibration that keeps that answer honest, asked at the moments he is
already standing still — at the water point, after market, when he comes back and
looks at the animals.

