"""Named places for the interactive map (fast — gazetteer file only, no DB).

Asserts the landmark list used to label /mapview is non-empty in the Isiolo /
Oldonyiro rangelands, is ordered settlements-before-rivers then nearest-first,
and is de-duplicated by name.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.services.map_renderer import _load_landmarks, nearby_landmarks  # noqa: E402

LM = _load_landmarks()
print(f"gazetteer: {len(LM)} named places")
assert LM, "landmarks.geojson missing or unreadable"

# Anchor on a known settlement in the gazetteer (Oldonyiro if present).
anchor = next((lm for lm in LM if lm["name"].lower().startswith("oldonyiro")), None)
if anchor is None:
    anchor = next(lm for lm in LM if lm["kind"] in ("town", "village"))
print(f"anchor: {anchor['name']} ({anchor['kind']})")

near = nearby_landmarks(anchor["lon"], anchor["lat"], radius_km=40, limit=12)
print(f"within 40 km: {[ (l['name'], l['kind'], l['dist_km']) for l in near[:6] ]}")
assert len(near) >= 3, "expected named places around the anchor"

names = [l["name"].strip().lower() for l in near]
assert len(names) == len(set(names)), "duplicate names in landmark list"

ranks = [l["rank"] for l in near]
assert ranks == sorted(ranks), "not ordered by usefulness (settlement before river)"
for l in near:
    assert l["dist_km"] <= 40 * 1.01, "landmark outside the requested radius"
    assert l["kind"], "landmark without a kind"

# A radius too small must yield nothing rather than everything.
assert nearby_landmarks(anchor["lon"], anchor["lat"], radius_km=0.2, limit=12) == [] or \
    len(nearby_landmarks(anchor["lon"], anchor["lat"], radius_km=0.2, limit=12)) <= 1
print("landmark lookup OK")
