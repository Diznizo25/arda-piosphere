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
