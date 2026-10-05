# The mentor layer

*What it is:* the model that turns our computed facts into something a pastoralist
reads the way he reads a person — and the guard that stops it saying anything we did
not measure.

*Why it exists:* the rules know what is true (a fodder band, an energy gap, a
parasite window, a water status) and rules write flat. "Anatumia MJ 50 … Upungufu MJ
24" is correct and cold. A herder acts on what a mentor tells him, so the model is
allowed exactly one job: **say the same facts like a mentor.**

## The rule that does not bend

**Rules compute. The model only rephrases.** The advisory had this from the start
(`ai.rephrase_advisory`); this layer generalises it to every service, because a model
that can touch the pest message needs the pest boundary enforced somewhere.

`mentor.rephrase_ok(base, out)` (the whole-text guard) and `mentor.insight_ok(base,
insight)` (the lead guard) throw a candidate away for any of these:

| Way to lose | Why it matters |
|---|---|
| `forbidden_word` | a drug, dose, injection, diagnosis or vaccine word — the boundary for all services, in one place |
| `dropped_number` | a lost figure is a lost fact |
| `new_number` | an invented figure is worse: it is a fact the herder now owns |
| `safety_label_lost` | if the source said *kadirio* (estimate) or named a vet, the rewrite must too |
| `language_flipped` | a Swahili message must not come back in English |
| `semicolon` / `colon_run` | the tell-tale of a model collapsing a list into one breathless run |
| `flattened` | the line breaks are the formatting; a data list must not become a paragraph |
| `markdown` / `new_url` | no formatting we did not send, no link we did not choose |
| `too_long` | padding is a behaviour change, not a rewrite |
| `empty` | a reply that says nothing must not replace one that says something |

When a rewrite is rejected, the **deterministic text goes out** — it is already
written to be read aloud — and the reason is logged. Fail-open is not a fallback
here, it is the normal case: `mentor_rewrite rejected (dropped_number:4.3)` in the
logs is the system working.

## Two shapes, and why both exist

The model may help in two ways, and using the wrong one is how the first version
failed live: the grazing message is dense with figures (bands, mega-joules, prices),
so a *whole-text* rewrite held to "keep every number" had nothing left to improve and
was rejected — the herder kept reading a table.

|  | `insight()` — 2–4 sentences | `rewrite()` — whole text |
|---|---|---|
| Use when | the text is a **data table** (the grazing ledger, the pest window) | the text is already **prose** (the welcome-back lines, the weekly note) |
| Numbers | optional — and any number used must exist in the text or the facts | **every** number must survive |
| Safety labels | not required, because the data text is sent underneath them | required (kadirio, a named vet) |
| Length | ≤ 5 short lines / ~420 chars | ≤ 900 chars |
| Guard | `insight_ok` | `rephrase_ok` |
| On failure | the data text ships alone | the original ships |

`voiced()` composes the insight **above** the data text:

```
Mlipopanda mbali kwa nyasi kavu, hivyo wanashiba lakini wanapungua.
Kwanza hamia km 4 kaskazini, kuna nyasi mbichi inayokua.

———
🌿 MALISHO YA LEO
📍 Mlipopanda ~km 14.0 kutoka manyatta yako
…every figure, unchanged…
```

That composition is the second safety property, and the test asserts it literally:
**the evidence block is the deterministic text verbatim.** An insight can use fewer
numbers, but it can never be the reason a herder loses one — and because the labels
travel with the figures they belong to, an insight cannot lose a label either.

The voice reply speaks the **insight**, not the table: a voice note reading out
mega-joules is noise, and those two sentences are what a herder acts on.

## Where it is applied — and where it deliberately is not

| Service | Shape | Why |
|---|---|---|
| Grazing ledger (`graze`) | insight + figures | bands and MJ are not how a herder thinks |
| Pest windows (`pest`) | insight + figures | conditions → "look here" reads best as advice |
| Welcome-back lines (`water`) | whole-text rewrite | already prose, and the clearest case of useful data reading like a report |
| Water advisory | already was, via `ai.rephrase_advisory` (stricter, distance-specific) | it predates this layer; unifying the guards is a follow-up |
| **Questions and menus** | **never** | a reworded question breaks the one-tap answer it belongs to: the state machine matches digits, and the herder answers what he *read* |
| Weekly note | whole-text rewrite, opt-in (`--mentor`) | one model call **per herder**: a 500-herder run is 500 calls, never silently on |

## The voice

`config/mentor_voice.yaml` (marked `needs_review`) holds the persona, the hard rules
the model is given, and a per-service `goal` — what THIS service's data means, so the
model explains the right thing instead of restating a list. The persona is one short
paragraph on purpose: a long persona is a licence to invent.

## Seeing it

```
POST /dev/mentor   X-Debug-Key: <WHATSAPP_VERIFY_TOKEN>
  {"kind":"graze","text":"<the deterministic message>","facts":{…},"lang":"swa"}
  {"kind":"pest","water_source_id":"<uuid>","lang":"swa"}      # builds the text for you
```

Returns `base`, `insight` (+ `insight_guard`), the composed `message` a herder would
receive, and the whole-text `rewrite` (+ `rewrite_used`, `rewrite_guard`) — so the
wording is reviewable as wording, and a rejection is visible rather than silent.
`scripts/check_mentor_live.py` prints all three side by side against production.

## The switch

`MENTOR_INSIGHTS_ENABLED=false` turns the whole layer off without a deploy: the
deterministic text goes out unchanged, everywhere.

## Tests

`scripts/test_mentor.py` asserts every row of the table above, the fail-open paths
(no model, a misbehaving model, the switch), that the facts come from the computed
objects rather than being made up, and that no question is ever rewritten.
