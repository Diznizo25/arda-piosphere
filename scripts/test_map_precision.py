"""Map precision: deep zoom, a plot drawn in metres, and honesty about resolution.

A herder wants to see the exact patch where his animals grazed. Two things have to be
true for that to be honest as well as useful:

  1. He can zoom right in (and the tiles must not go grey when he does).
  2. Anything drawn is drawn at a REAL size on the ground. A fixed pixel dot claims a
     different area at every zoom level — the closer he looks, the bigger the lie.
  3. The page says what our data actually is: 10 m pixels, an area mean. Zooming shows
     the ground, not more precision from us.

Run: python scripts/test_map_precision.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, ".")

root = Path(__file__).resolve().parents[1]
mv = io.open(root / "app/routers/mapview.py", encoding="utf-8").read()
gr = io.open(root / "app/routers/grazing.py", encoding="utf-8").read()

# --- 1) zoom like a map app ------------------------------------------------
assert "maxZoom: 22" in mv, "the map must zoom past the satellite's native level"
assert "const ESRI_NATIVE = 18" in mv and "maxNativeZoom: ESRI_NATIVE" in mv, \
    "the satellite must stop requesting tiles at the level where Isiolo's imagery ends"
assert "maxNativeZoom: 19" not in mv, \
    "z19 returns Esri's byte-identical 'Map data not yet available' placeholder here"
assert "deepNote" in mv and "applyDepthNotes" in mv and "g_stretch" in mv, \
    "past the native limit the page must say the picture is stretched, not blank"
assert "L.control.scale(" in mv and "metric: true" in mv, \
    "no scale bar means no readable precision on the ground"
assert "zoomControl: true" in mv

# --- 2) the grazed patch: real size, sized by him, measured back to him -----
assert "L.circle(spot," in mv, \
    "the grazed patch must be drawn at a real size on the ground, in metres"
assert "radius: radius" in mv, "the circle must use the radius he chose"
assert "L.circleMarker(ev.latlng" not in mv, \
    "the old pixel-sized dot lied about area at different zooms"
assert "const SIZES = [50, 100, 250, 500]" in mv, "he must be able to size the patch"
assert "circle.setRadius(radius)" in mv, "changing the size must redraw the patch"
assert "Math.PI * radius * radius" in mv and "/ 10000" in mv, \
    "hectares must be computed from the drawn radius"
assert "toFixed(1)" in mv, "the hectares must be shown"
assert "toFixed(5)" in mv, "the position must be readable to about a metre"
assert "radius_m: radius" in mv, "the patch size must be sent with the pin"

# --- 3) honesty: our reading is coarser than the imagery he zooms into ------
assert '"g_data"' in mv and "pikseli 10 m" in mv, \
    "the page must state that the vegetation reading is 10 m per pixel, an area mean"
assert "wastani wa eneo" in mv, "...and that the value is an area mean, not a plant"

# --- 4) the endpoint bounds and echoes the size, and never silently drops it -
assert "10.0 <= radius_m <= 5000.0" in gr, "the pin endpoint must bound the radius"
assert '"radius_m": radius_m' in gr, "the endpoint must echo the size it accepted"
assert "grazing pin: plot radius" in gr, \
    "and log it, because it is not stored per event yet (a column is a separate step)"

# --- 5) no green chrome was introduced (the palette rule lives in test_map_palette) --
assert "background:#16a34a" not in mv, "a green button reads as 'this is good grass'"

# --- 6) what he sees as he zooms: the colour steps aside, the names arrive -----
assert "const DEEP_ZOOM = 16" in mv and "if (z > 15 && !overlayForced)" in mv, \
    "the pasture colour must leave when he is close enough to look for his own plot"
assert "overlayForced" in mv, "...and one tap on the toggle must still bring it back"
assert "map.on('zoomend'" in mv and "applyOverlayStyle(); applyLabels();" in mv, \
    "both depth rules must run when he zooms"
assert "const NAME_ZOOM = 12" in mv and "function applyLabels()" in mv, \
    "names appear when he is close and stand down when he is far"
assert "addName(marker, label + d)" in mv, \
    "every water point must wear the name he gave it"
assert "it.layer.unbindTooltip()" in mv, "labels must switch off again when zoomed out"
assert "bindTooltip(rv.name" in mv, \
    "river labels stay always-on: they are how he orients from far away"

# --- 7) finding himself on the ground -----------------------------------------
assert "id = 'wherebtn'" in mv and "map.locate(" in mv, "no 'where am I' control"
assert "enableHighAccuracy: true" in mv, \
    "a coarse fix is useless for finding one plot on the ground"
assert "locationerror" in mv and "TXT.g_where_no" in mv, \
    "a refused location must say so, not fail silently"
assert "map.distance(latlng, to)" in mv and "bearingWord(" in mv, \
    "he must be told how far and which way the point is"
assert "COMPASS = ['kaskazini'" in mv, "direction words must be the Swahili ones he uses"
assert "/ 5 * 60" in mv, "walking time matters to him, not raw kilometres"
assert '"g_where": "Niko wapi?"' in mv, "the control needs its Swahili wording"

# --- 8) close-up imagery: sharp where we flew, honest blur where we did not ----
st = io.open(root / "app/services/storage.py", encoding="utf-8").read()
mp = io.open(root / "app/routers/maps.py", encoding="utf-8").read()
mr = io.open(root / "app/services/map_renderer.py", encoding="utf-8").read()
assert 'return f"ortho/{water_source_id}/ortho.png"' in st, \
    "the close-up contract (one key per water point) is missing"
assert '"/{water_source_id}/ortho.png"' in mp, "nothing serves a close-up image"
assert "def ortho_available(" in mr and "return False" in mr, \
    "availability must fail open: a missing image means no close-up, never a broken map"
assert "ortho_available(main_id)" in mv and '"ortho": ortho' in mv, \
    "the map payload must carry the close-up only when one exists"
assert "orthoLayer" in mv and "TXT.g_close" in mv, \
    "a close-up must be shown above the satellite's real limit, and named as what it is"
assert '"g_close"' in mv, "the close-up chip needs its Swahili wording"
doc = io.open(root / "docs/map_clarity.md", encoding="utf-8").read()
for needle in ("z18", "0.6 m", "3–10 cm", "Photos herders send", "Scrape Google",
               "blank pixels", "we do not guess"):
    assert needle in doc, f"docs/map_clarity.md must state {needle!r}"

# --- 9) the real ground loads first, and the blanks are explained --------------
assert "esriTiles.addTo(map)" in mv, \
    "the satellite must be the layer that loads first — the ground he recognises"
assert "[TXT.satBase || 'Satellite']: esriTiles" in mv, \
    "satellite first in the switcher, with the street map still one tap away"
assert "osmTiles).addTo(map)" not in mv, "the street map must not be the default any more"
assert mv.count("L.control.layers(") == 1, "one base-map switcher, not two"
assert '"no_read"' in mv and "hatukisia" in mv, \
    "the legend must say why a pixel is blank (we never guess a pixel)"
assert '"ring_clip"' in mv, \
    "the legend must say the colour is clipped inside the reach line"
# The blanks are quantified, and the share is of HIS ring — computed where the mask is.
assert 'out["blank_pct"]' in io.open(
    root / "app/services/map_renderer.py", encoding="utf-8").read(), \
    "the blank share of the herder's ring must be computed, not guessed"
assert "p.blank_pct" in mv, "the legend must show the blank share it was given"

print("map precision: deep zoom, metre-sized plot, hectares shown, resolution stated OK")
