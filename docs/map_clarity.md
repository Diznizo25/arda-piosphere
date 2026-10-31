# Map clarity: why deep zoom blurs, and what would actually fix it

*The question a herder asks: "can it be clear when I zoom right in?" Answer: not from a
satellite. Here are the numbers, and the three routes that do work.*

## The measured facts

| Source | Detail in Isiolo | Where it stops |
|---|---|---|
| **Esri World Imagery** (our satellite layer) | **~0.6 m per pixel** | Real tiles to **z18**; z19+ returns a byte-identical "Map data not yet available" placeholder |
| OpenStreetMap (our street layer) | vector-ish; near-empty in the bush | Names and roads in towns, nothing on rangeland |
| Google Maps (for comparison) | often 0.3–0.5 m in towns, plus Street View | Also **interpolated** past ~z20 — it only *feels* sharper because the source is sharper and the app smooths it |
| Sentinel-2 (our own rasters) | **10 m per pixel** | Our vegetation reading; deliberately not a zoom source |

Beyond z18 we stretch the last real picture. At z20 that is 4 screen pixels per image
pixel, at z22 it is 16. **No code can put detail back** — interpolation is arithmetic, not
information. So "clear at very deep zoom" is not a bug we can fix; it is a source limit.

## What *would* be clear

| Route | Detail | Cost | Honest verdict |
|---|---|---|---|
| **1. Photos herders send** | Ground level — sharper than any satellite for *recognising* a spot | ~zero (they already have WhatsApp) | **Do this first.** A photo of the trough, the tree, the track answers "is this my place?" better than 0.1 m imagery, and it doubles as water-status evidence |
| **2. Drone / aerial survey of the points that matter** | **3–10 cm** per pixel — 60–200× finer than now | One survey day per ward cluster + processing; a Kenyan operator, not a software project | **The only real fix for plot-level clarity.** Sharp exactly where we fly, honest blur everywhere else |
| **3. Commercial satellite archive** (Maxar/Planet-class) | **0.3–0.5 m** | Thousands of USD per area, licensed, redistribution to herders restricted | **Only ~2× sharper than what we already show.** Only worth it if a buyer (county/NGO) funds it — and it still blurs when stretched |

## The plumbing is already in place for route 2

Whenever an orthophoto exists, the map uses it automatically — no code change needed:

* **Contract:** a PNG at `ortho/<water_source_id>/ortho.png` in R2, covering **the same
  footprint as the pasture layer** (same bounds), served at `GET /map/<id>/ortho.png`.
* **Behaviour:** above z18 — where the satellite is stretched — the map swaps in the
  close-up (opacity 1) and the chip changes from *"Satelaiti imepanuliwa"* to *"Picha ya
  karibu ya chanzo hiki"*. Below z18, or when no image exists, nothing changes and the
  stretched imagery keeps its honest label.
* **Fail-open:** `map_renderer.ortho_available()` returns False on any error, so a missing
  or broken image can only mean "no close-up", never a broken map.

So the day you fly Oldonyiro's water points, the map gets sharp there immediately, and the
ward it has not flown keeps telling the truth about its own blur.

## What the blank pixels mean

Not every pixel is coloured, and that is deliberate:

* **The satellite could not be read there** — a cloud, a shadow, or no clean pass that
  week. We leave the pixel transparent rather than guess, because a guessed colour is a
  herder walking to grass that is not there.
* **Outside the grazing reach** — the colour is clipped inside the species' reach line
  (the dashed ring), so it matches exactly what the rings say. Beyond it, no colour.

The live map now says both of these in the legend, in Swahili, next to the colours — and
gives the blank share **of his own ring** (e.g. "Sehemu zisizo na rangi: 18% ya upeo wako
hatukuweza kuzisoma — hatukisia"): a herder who understands why a patch is blank trusts
the patches that are not. A few gaps look like a few gaps; a clouded week looks like a
clouded week. The rule behind it: **we do not guess**, we do not colour a pixel we cannot
read.

## How strongly the pasture colour is painted (and why bare is a whisper)

The palette is fixed — green = grass, brown = dry forage, grey = bare, red = water —
but *how opaque* each class is decides whether a herder can see the answer at all. It
was measured, not guessed, by compositing the served layer over the real Esri tiles at
the browser's own opacity:

| Class | What it covers | Alpha x opacity | Pixels move by |
|---|---|---|---|
| grass | ~6% of a ring | 255 x 0.85 | **~44/255** |
| dry forage | ~15% | 255 x 0.85 | **~23/255** |
| bare (now) | ~79% | 95 x 0.85 | ~6/255 (a flat, colourless patch) |
| bare (before) | ~79% | 150 x 0.70 | ~14/255 — which is *below* what a phone in the sun shows, while still bleaching the ground and turning dry forage into mud |

Two rules follow:

* **The classes he acts on shout; bare is a whisper.** Bare is 79% of a typical ring, so
  a heavy grey there is not a colour code, it is fog over four fifths of the map — and it
  buries the 15% of dry forage he is actually looking for.
* **The colour never leaves the map.** It used to be *removed* above z15 ("one 10 m pixel
  fills the screen"), i.e. exactly when he zooms in to look at his own grazing ground —
  the moment he most wants it. Now it fades with depth (0.85 → 0.7 → 0.55) and the
  "Ficha malisho / Onyesha malisho" button owns the only on/off decision.

Both are pinned by `scripts/test_pasture_layer_geo.py` (the alpha ladder),
`scripts/test_mapview.py` (no removal at depth, one `addLayer`) and
`scripts/check_map_live.py` (the served page).

## What we will not do

* **Scrape Google's tiles.** Better imagery exists behind their terms; using it would put
  the whole service — and the herders' data — at risk.
* **Fake sharpness.** No AI upscaling of satellite tiles: it would invent bomas, tracks
  and troughs that are not there, and a herder walking to a hallucinated landmark is worse
  than a herd that walked to a blurry one.
* **Call a stretched picture "close-up".** The chip names what he is looking at.

## Recommendation, in order

1. **Photo points** — herder-sent photos attached to a water point, shown as tappable
   markers. Cheapest, sharpest for recognition, and evidence the data buyers want.
2. **Fly the pilot ward's water points** (~5–10 points, 1–2 km² each) for genuine
   plot-level clarity where it matters most, and validate the contract above with real
   imagery.
3. **Paid imagery only against a buyer's line** — and only after 1 and 2 show what the
   herder actually needs to recognise.
