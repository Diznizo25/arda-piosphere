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

`mentor.insight_ok(base, out)` throws a rewrite away for any of these:

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

## Where it is applied — and where it deliberately is not

| Service | Rewritten | Why |
|---|---|---|
| Grazing ledger (`graze`) | yes | the whole point: bands and MJ are not how a herder thinks |
| Pest windows (`pest`) | yes | conditions → "look here" reads best as advice |
| Welcome-back lines (`water`) | yes | "what changed while you were away" is the clearest case of useful data reading like a report |
| Water advisory | already was, by `ai.rephrase_advisory` — a stricter, distance-specific guard | it predates this layer; unifying the two guards is a follow-up, not an emergency |
| **Questions and menus** | **never** | a reworded question breaks the one-tap answer it belongs to: the state machine matches digits, and the herder answers what he *read* |
| Weekly note | opt-in (`--mentor`) | one model call **per herder**: a 500-herder run is 500 calls, so it is never silently switched on |

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

Returns `base`, `mentor`, `changed` and the guard's `guard` reason — both texts side
by side, so the wording is reviewable as wording.

## The switch

`MENTOR_INSIGHTS_ENABLED=false` turns the whole layer off without a deploy: the
deterministic text goes out unchanged, everywhere.

## Tests

`scripts/test_mentor.py` asserts every row of the table above, the fail-open paths
(no model, a misbehaving model, the switch), that the facts come from the computed
objects rather than being made up, and that no question is ever rewritten.
