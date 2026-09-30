"""One hue = one meaning on the map.

A herder reads the map for two seconds on a small screen. If green means grass AND the
shoat ring AND the daily-zone line AND the "go this way" arrow — which is what shipped,
five greens with four meanings — the map stops communicating and starts confusing.

This suite pins the palette rule so it cannot drift back:
  * green  = PASTURE QUALITY only,
  * blue   = WATER only,
  * red    = your own water point (and the "return to water" warning),
  * purple / magenta / orange = the three species rings,
  * white and near-black = pure geometry (zone limits, direction arrow) — no hue,
  * every water marker is one blue, and the label names the type.

It checks the constants AND both renderers (the WhatsApp PNG and the live map's JS), so
the two can never disagree about what a colour means.
"""
import io
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.services import map_renderer as mr  # noqa: E402


def is_green(c) -> bool:
    r, g, b = c[0], c[1], c[2]
    return g > r + 25 and g > b + 25


def is_blue(c) -> bool:
    """Sky/water blue: blue dominant with little red (purple and magenta are not blue)."""
    r, g, b = c[0], c[1], c[2]
    return b > r + 40 and b > g + 25 and r < 110


def is_red(c) -> bool:
    r, g, b = c[0], c[1], c[2]
    return r > g + 60 and r > b + 60


def is_neutral(c) -> bool:
    r, g, b = c[0], c[1], c[2]
    return max(r, g, b) - min(r, g, b) <= 30


# --- 1) roles, by construction ---------------------------------------------
assert mr.PASTURE_GREEN and is_green(mr.PASTURE_GREEN), "pasture green must be green"
assert is_blue(mr.WATER_COLOR), "water must be blue"
assert is_red(mr.WATER_MINE), "the herder's own point must stand out in red"
assert is_neutral(mr.ZONE_LIMIT_COLOR), "zone limits must claim no hue"
assert is_neutral(mr.ZONE_LIMIT_HALO), "the zone-limit halo must be neutral"
assert is_neutral(mr.ARROW_COLOR), "the direction arrow must claim no hue"
for name, style in mr.RING_STYLE.items():
    outline = style[1]
    assert not is_green(outline), f"{name} ring must not be green (green = pasture)"
    assert not is_blue(outline), f"{name} ring must not be blue (blue = water)"
print("roles: green=pasture, blue=water, red=mine, rings=own hues, geometry=neutral")

# --- 2) no two roles share a colour ---------------------------------------
used = [mr.PASTURE_GREEN, mr.PASTURE_DRY, mr.PASTURE_BARE, mr.WATER_COLOR, mr.WATER_MINE,
        mr.ZONE_LIMIT_COLOR, mr.ARROW_COLOR] + \
       [s[1] for s in mr.RING_STYLE.values()]

def close(a, b, tol=40) -> bool:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) < tol

for i, a in enumerate(used):
    for b in used[i + 1:]:
        assert not close(a, b), f"two roles share a colour: {a} vs {b}"
assert len({s[1] for s in mr.RING_STYLE.values()}) == 3, "ring colours must differ"
print("palette: every role has its own colour, rings are distinct")

# --- 3) the WhatsApp renderer follows the rule ----------------------------
src = io.open("app/services/map_renderer.py", encoding="utf-8").read()
assert "WATER_TYPE_COLOR" not in src, "the five water-marker colours are back"
assert "draw.polygon(zpts, outline=ZONE_LIMIT_HALO" in src, \
    "zone limits must be drawn with their neutral halo"
assert "fill=ARROW_COLOR + (255,)" in src, "the arrow must use the neutral arrow colour"
assert "palette: ONE HUE = ONE MEANING" in src, "the palette rationale must stay in the module"
assert "kijani = nyasi" in src, "the legend must say what green means"
assert "mstari mweupe" in src, "the legend must explain the white line"
assert "mshale mweusi" in src, "the legend must explain the black arrow"
print("png renderer: one water colour, neutral geometry, legend explains it")

# --- 4) the live map follows the same rule --------------------------------
mv = io.open("app/routers/mapview.py", encoding="utf-8").read()
assert "typeColor" not in mv, "the per-type water colours are back in the live map"
assert "const WATER_HEX = '#2563eb'" in mv, "the live map must use one water colour"
for ring in ('"cattle": "#7c3aed"', '"shoat": "#db2777"', '"camel": "#ea580c"'):
    assert ring in mv, f"live-map ring colour missing: {ring}"
assert "comfortable: '#ffffff'" in mv and "far: '#ffffff'" in mv, \
    "live-map zone limits must be neutral too"
# No green may appear outside the pasture palette in either file.
for label, text in (("map_renderer.py", src), ("mapview.py", mv)):
    green_hexes = [h for h in ("#16a34a", "#059669", "#10b981", "#22c55e", "#065f46")
                   if h in text]
    allowed = {"#16a34a"} if label == "mapview.py" else set()
    stray = set(green_hexes) - allowed
    assert not stray, f"{label} uses green for something other than pasture: {stray}"
print("live map: same palette, no stray green")

print("\nMAP PALETTE OK")
