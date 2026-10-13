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
assert mv.count("maxNativeZoom: 19") >= 2, \
    "tiles must not go grey when he zooms past the native level"
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

print("map precision: deep zoom, metre-sized plot, hectares shown, resolution stated OK")
