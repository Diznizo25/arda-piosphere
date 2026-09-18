# ADR 002 — One forage classifier, and greenness wins outright

**Status:** accepted · **Phase:** 1 · **Date:** 2026-09-18

## Context

There were two forage classifiers with different precedence rules:

- `advisory_logic.classify_forage_condition` — a scalar `if/elif` chain where
  NDVI at or above the green threshold wins outright.
- `map_renderer._nearest_good_patch` — vectorised **sequential assignment**,
  where `classes[... satvi < bare] = 3` and `classes[... bsi >= bare] = 3` ran
  *after* green had been assigned, so either could repaint a green pixel red.

Sweeping the plausible NDVI × SATVI × BSI domain (76,176 cells), they disagreed
on **52.7% of everything the text called "green"** — 22.2% via the SATVI rule,
30.4% via the BSI rule.

The realistic case is riparian vegetation: dense green canopy along the Ewaso
has low SWIR reflectance, so SATVI falls and BSI rises. That strip is the best
grazing in the ward. The words called it fresh growing pasture; the map in the
same WhatsApp reply painted it red for bare ground.

Separately, the scalar path had no NODATA state. An all-NaN read fell through to
`UNCERTAIN` and then set `seasonally_normal = (nan >= 35)` → `False`, which
produced *"Hali hii ni mbaya zaidi ya kawaida kwa msimu huu"* — telling a herder
conditions were worse than usual on the strength of bands never read.

## Decision

`app/services/forage.py` is the only classifier. Both paths call it.

Precedence is an **order**, expressed with `np.select` (first-match-wins) so no
later rule can silently overwrite an earlier one:

1. `green` — NDVI at/above the green threshold. **Wins outright.**
2. `dry` — SATVI at/above dry-forage AND BSI below bare.
3. `bare` — SATVI below bare OR BSI at/above bare.
4. `uncertain` — anything left.

Any non-finite input yields `NODATA`, which is never coloured, never counted in
a percentage, and never averaged into an answer. `seasonally_normal` becomes
tri-state: `True` / `False` / `None` for "not known".

## Why greenness wins

Direct evidence of photosynthesising vegetation is the strongest signal we
have. SATVI and BSI exist to rescue forage that greenness *misses* (cured
standing grass), not to overrule greenness where it is present. Treating them
as vetoes inverts the reason the system computes them.

## Consequences

- The map stops contradicting the text. Verified: 0 mismatches / 76,176 cells
  against the previous *text* behaviour, so this is not a change in forage
  science — only in which of the two existing answers survives.
- `scripts/test_pasture_logic.py::test_classification` asserted the old
  sequential-overwrite behaviour and has been superseded by `tests/test_forage.py`.
- Shadow mode (Phase 1 demonstration) measures the disagreement rate on **real
  pixels** before the herder-visible switch. The 52.7% figure is a uniform
  domain sweep and is an upper bound, not a traffic estimate.

## What would change our mind

If shadow mode shows greenness-wins produces materially worse agreement with
herder ground-truth reports than the map's rule did, revisit — but with
measured labels, not by reverting to two classifiers.
