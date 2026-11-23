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
import re
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
    green_hexes = [h for h in ("#15803d", "#16a34a", "#059669", "#10b981", "#22c55e",
                               "#065f46")
                   if h in text]
    allowed = {"#15803d"} if label == "mapview.py" else set()
    stray = set(green_hexes) - allowed
    assert not stray, f"{label} uses green for something other than pasture: {stray}"
# Green is a ROLE, not a decoration: in the live map the pasture hex may appear exactly
# once — as the pasture colour map. A green button or banner tells a herder "this is good
# grass" while asking him for input. (This rule caught the map's own Send button.)
assert mv.count("#15803d") == 1, \
    f"mapview.py uses the pasture green {mv.count('#15803d')} times; green is pasture only"

# --- 5) the KEY must be the PAINT ------------------------------------------
# A swatch that is a different green from every pixel of the layer teaches the wrong
# colour. The live key used #16a34a while the layer painted #15803d, so it is now
# parsed out of the page and compared with the renderer's own constants.
m = re.search(r"const pastureCols = \{ green:'(#[0-9a-f]{6})', dry:'(#[0-9a-f]{6})', "
              r"bare:'(#[0-9a-f]{6})' \};", mv)
assert m, "the live map's pasture colour key is gone"
hexed = lambda c: "#%02x%02x%02x" % (c[0], c[1], c[2])  # noqa: E731
want = (hexed(mr.PASTURE_GREEN), hexed(mr.PASTURE_DRY), hexed(mr.PASTURE_BARE))
assert tuple(m.groups()) == want, f"the key ({tuple(m.groups())}) is not the paint ({want})"

# The static PNG key is drawn from the constants themselves, so it cannot drift; what
# can drift is WHERE the colour goes. Two rules, pinned in the source:
assert "fill = style[0] if (active and not pasture_available) else None" in src, \
    "the active ring's tint is painting over the pasture classes again"
assert "sampled[km > radius_km] = 0" in src, \
    "the PNG's pasture colour is no longer clipped to the rings its key names"
print("live map: same palette, no stray green, key == paint")

print("\nMAP PALETTE OK")
