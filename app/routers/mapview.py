"""Public interactive map page (Google-Maps-style).

`GET /mapview/` returns a mobile, zoomable Leaflet map (OpenStreetMap tiles)
showing:
  * the herder's blue "Wewe hapa (place)" pin
  * every nearby water point as a type-coloured pin (numbered when a list was
    shared) — TAPPING a pin opens THAT water point's species rings + satellite
    pasture layer
  * the species rings of the water point in `id=`, or — when no id is given —
    of the NEAREST point, so the rings and the colour key are on the map as soon
    as it opens (graze mode and the numbered choice map excepted, where the tap
    or the numbered list is the question being asked)
  * the transparent pasture layer + a quantity/quality summary in the legend

The link is shared from WhatsApp after maps/advisories.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.services import map_renderer, water_reach, water_sources

router = APIRouter(prefix="/mapview", tags=["mapview"])
_log = logging.getLogger(__name__)

# One hue = one meaning, matching app/services/map_renderer.py so the live map and
# the WhatsApp map can never disagree: green is PASTURE only, blue is WATER only,
# rings are purple/magenta/orange, and pure geometry (zone limits) is neutral.
_RING_HEX = {"cattle": "#7c3aed", "shoat": "#db2777", "camel": "#ea580c"}


def _t(lang: str) -> dict:
    if lang == "eng":
        return {
            "title": "Live map — pasture & water",
            "you": "You are here",
            "tap": "Tap a pin to see its rings & pasture. Pinch to zoom.",
            "show": "Show rings + pasture",
            "pasture": "Satellite pasture",
            "summary": "Pasture now",
            "grass": "green = grass", "dry": "brown = dry forage",
            "bare": "red = bare", "on": "Hide pasture", "off": "Show pasture",
            "preparing": "Pasture layer being prepared",
            "places": "Named places", "cattle": "cattle", "shoat": "goats/sheep",
            "camel": "camels", "k_city": "town", "k_village": "village",
            "k_market": "market", "k_river": "river", "k_peak": "hill",
            "rivers": "Rivers (water) — zones follow the river",
            "z_near": "near water", "z_edge": "edge of zone",
            "z_limit": "far limit — return to water",
            "water_status": "Water status (from herders)",
            "s_ok": "water there", "s_season": "seasonal water",
            "s_dry": "dry (reported)", "s_broken": "broken (reported)",
            "s_gone": "gone (reported)", "s_unknown": "not confirmed",
            "mapBase": "Map", "satBase": "Satellite",
            "g_hint": "Tap where they grazed today — inside the pasture colours (green or brown). That is where we measure the grass.",
            "g_sending": "Sending…",
            "g_sent": "Thank you! The answer has been sent to WhatsApp.",
            "g_fail": "That did not work. Open the link again from WhatsApp.",
        }
    return {
        "title": "Ramani hai — malisho na maji",
        "you": "Wewe hapa",
        "tap": "Bonyeza alama ya maji kuona kanda na malisho yake. Piga zoom.",
        "show": "Ona kanda + malisho",
        "pasture": "Malisho ya satelaiti",
        "summary": "Malisho sasa hivi",
        "grass": "kijani = nyasi", "dry": "kahawia = nyasi kavu",
        "bare": "nyekundu = tupu", "on": "Ficha malisho", "off": "Onyesha malisho",
        "preparing": "Ramani ya malisho inaandaliwa",
        "places": "Maeneo yenye majina", "cattle": "Ng'ombe", "shoat": "Mbuzi/Kondoo",
        "camel": "Ngamia", "k_city": "Mji", "k_village": "Kijiji",
        "k_market": "Soko", "k_river": "Mto", "k_peak": "Kilima",
        "rivers": "Mito (maji) — kanda zinafuata mto",
        "z_near": "karibu na maji", "z_edge": "ukingo wa eneo",
        "z_limit": "kikomo cha mbali — rudi majini",
        "water_status": "Hali ya maji (kutoka wachungaji)",
        "s_ok": "maji yapo", "s_season": "maji ya vipindi",
        "s_dry": "imekauka (taarifa)", "s_broken": "imeharibika (taarifa)",
        "s_gone": "haipo tena (taarifa)", "s_unknown": "haijathibitishwa",
        "mapBase": "Ramani", "satBase": "Satellite",
        # Grazing-ledger mode (opened from WhatsApp with graze=1&t=<token>).
        "g_hint": "Gusa mahali mlipopanda leo — ndani ya rangi ya malisho (kijani au kahawia). Hapo tu tunapima nyasi.",
        "g_sending": "Inatuma…",
        "g_sent": "Asante! Jibu limekutumwa kwenye WhatsApp.",
        "g_fail": "Imeshindikana. Fungua link tena kutoka WhatsApp.",
        # The plot: he sizes it, so the number he sees is the area he is telling us about.
        "g_size": "Ukubwa wa eneo",
        "g_send": "Tuma eneo hili",
        "g_across": "upana",
        "g_ha": "hekta",
        "g_zoom": "Piga zoom hadi uone eneo lako vizuri, kisha gusa katikati yake.",
        # Our vegetation reading is 10 m per pixel and is an area mean: zooming in shows
        # the ground, not more precision from us.
        "g_data": "Data yetu: pikseli 10 m; thamani ni wastani wa eneo, si ya mti mmoja.",
        # Finding himself on the ground: Google Maps does it with photos and street names;
        # here it is his own position, the names he told us, and the ground itself.
        "g_where": "Niko wapi?",
        "g_where_no": "Simu hii haikuruhusu eneo",
        "g_recenter": "Nioneshe tena",
        "g_from": "kutoka",
        "g_walk": "tembea kama dakika",
        "g_here": "Uko hapa (sasa)",
        "g_ground": "Kwa zoom kubwa rangi huondoka ili uone ardhi yenyewe.",
        # The satellite runs out at z18 in Isiolo: past that we stretch the last real
        # picture, and he is told the ground he is looking at is stretched.
        "g_stretch": "Satelaiti imepanuliwa (kina cha juu ~0.6 m)",
        # If somebody flew this point (drone/aerial), the page swaps in the sharp image.
        "g_close": "Picha ya karibu ya chanzo hiki (utata wa juu)",
        # Blank pixels are a decision, not a fault: we never colour a pixel we cannot read.
        "no_read": "Sehemu zisizo na rangi",
        "no_read_why": "ya upeo wako hatukuweza kuzisoma",
        "no_guess": "hatukisia (wingu, kivuli, au hakuna picha safi)",
        "ring_clip": "Rangi zinaonekana ndani ya mstari wa kufikia tu (mstari wenye vitone).",
    }


def _as_feature(geojson: dict) -> dict:
    """Normalise a stored ring (bare geometry) into a GeoJSON Feature.

    `piosphere_zones.geojson` is `st_asgeojson(geom)` — a bare Polygon. The page
    reads `ring.geojson.geometry` when it fits the rings on screen, so a bare
    geometry left that undefined and the rings could not be framed; and
    `_scale_geojson_ring` had no `geometry` to scale, so the effective-reach
    scaling silently drew the full-size ring while the legend said otherwise.
    """
    kind = geojson.get("type")
    if kind == "Feature":
        return geojson
    if kind == "FeatureCollection":
        feats = geojson.get("features") or []
        return feats[0] if feats else {"type": "Feature", "properties": {},
                                       "geometry": None}
    return {"type": "Feature", "properties": {}, "geometry": geojson}


def _scale_geojson_ring(geojson: dict, frac: float) -> dict:
    """Scale a ring polygon toward its own centre (effective reach at draw time)."""

    def scale_coords(coords):
        xs = [p[0] for p in coords]
        ys = [p[1] for p in coords]
        cx = (min(xs) + max(xs)) / 2.0
        cy = (min(ys) + max(ys)) / 2.0
        return [[cx + (x - cx) * frac, cy + (y - cy) * frac] for x, y in coords]

    feature = _as_feature(geojson)
    g = feature.get("geometry") or {}
    if g.get("type") == "Polygon":
        g = {**g, "coordinates": [scale_coords(r) for r in g["coordinates"]]}
    elif g.get("type") == "MultiPolygon":
        g = {**g, "coordinates": [[scale_coords(r) for r in poly]
                                  for poly in g["coordinates"]]}
    return {**feature, "geometry": g}


def _payload(lat: float, lon: float, species: str, interval: str,
             water_id: str | None, numbered: str | None, name: str | None,
             lang: str, auto_focus: bool = True) -> dict:
    text = _t(lang)
    numbered_ids = [u for u in (numbered or "").split(",") if u]
    by_id: dict[str, water_sources.WaterSource] = {}
    try:
        by_id = {w.id: w for w in water_sources.list_water_sources()}
    except Exception:  # noqa: BLE001
        by_id = {}
    # Nearby options: numbered list when shared, else the nearest points.
    options: list[dict] = []
    if numbered_ids:
        for i, wid in enumerate(numbered_ids, start=1):
            w = by_id.get(wid)
            if w:
                options.append({"id": wid, "lon": w.lon, "lat": w.lat,
                                "name": w.name, "water_type": w.water_type,
                                "ward": w.ward, "num": i,
                                "dist_km": round(map_renderer._haversine_km(
                                    lat, lon, w.lat, w.lon), 1)})
    else:
        for n in water_reach.list_nearby_water_sources(lon, lat, limit=10):
            options.append({"id": n["water_source_id"], "lon": n["lon"],
                            "lat": n["lat"], "name": n["name"],
                            "water_type": n["water_type"], "ward": n["ward"],
                            "num": None, "dist_km": n["distance_km"],
                            "status": n.get("status"),
                            "status_label": n.get("status_label"),
                            "status_age_days": n.get("status_age_days"),
                            "needs_check": n.get("needs_check")})

    # Rings + pasture for ONE water point: the one asked for, otherwise the
    # nearest one — so the rings and the colour key are on the map the moment it
    # opens.
    #
    # They used to appear only after a pin tap, which meant the page reached from
    # the menu was a handful of blue dots with no rings and no colour legend: it
    # looked like the data had gone missing. The numbered choice map is the one
    # exception — there the NUMBERED markers are the question being asked, and
    # focusing one of them would hide the choice.
    main_id = water_id if (water_id and water_id in by_id) else None
    if main_id is None and auto_focus and not numbered_ids and options:
        main_id = options[0]["id"]  # options are ordered nearest-first
    rings: list[dict] = []
    eff_km: float | None = None
    overlay: dict | None = None
    ortho: dict | None = None
    if main_id:
        # Two separate blocks on purpose: the rings are cheap and come from the
        # database, the pasture summary reads a satellite COG. Sharing one
        # try/except meant a COG hiccup silently erased the rings as well, and
        # the map lost both the rings and the colour key at once.
        try:
            from app.config import get_species_rings

            rings_cfg = get_species_rings()
            eff_km = rings_cfg.effective_radius_km(species, interval)
            zones = water_sources.zones_for_water_source(main_id)
            for z in zones:
                gj = _as_feature(json.loads(z["geojson"]))
                km = float(z["radius_km"])
                if z["species"] == species and km > 0:
                    gj = _scale_geojson_ring(gj, eff_km / km)
                    km = eff_km
                rings.append({"species": z["species"], "km": km,
                              "color": _RING_HEX.get(z["species"], "#64748b"),
                              "geojson": gj})
        except Exception:  # noqa: BLE001
            _log.exception("mapview: species rings failed for %s", main_id)
            rings, eff_km = [], None
        try:
            status = map_renderer.pasture_overlay_status(main_id, species, interval)
            if status:
                from app.config import get_settings

                base = get_settings().app_public_base_url.rstrip("/")
                overlay = {
                    "url": (f"{base}/map/{main_id}/pasture.png?species={species}"
                            f"&interval={interval}&v=10"),
                    "bounds": status["bounds"],
                    "available": status["available"],
                    "usable_pct": status["usable_pct"],
                    "frac": status["frac"],
                    # How much of HIS reach we could not read. Without this the legend can
                    # only say "blank means unreadable"; with it he sees how many of the
                    # blank patches are his problem this week.
                    "blank_pct": status.get("blank_pct"),
                }
                # A close-up image for the SAME footprint, when somebody has flown it.
                # Above z18 the satellite is stretched, so this is the swap that makes a
                # plot genuinely sharp — and it stays absent where we have no such image.
                if map_renderer.ortho_available(main_id):
                    ortho = {
                        "url": f"{base}/map/{main_id}/ortho.png",
                        "bounds": status["bounds"],
                    }
        except Exception:  # noqa: BLE001
            _log.exception("mapview: pasture summary failed for %s", main_id)
            overlay, ortho = None, None

    # Named places around the map (villages, markets, rivers, peaks) so the
    # pasture colours can be read against names a pastoralist actually uses.
    landmarks: list[dict] = []
    try:
        anchor_lon, anchor_lat = lon, lat
        if main_id and main_id in by_id:
            w = by_id[main_id]
            anchor_lon, anchor_lat = w.lon, w.lat
        km = max([r["km"] for r in rings], default=0.0) or 25.0
        landmarks = map_renderer.nearby_landmarks(anchor_lon, anchor_lat,
                                                  radius_km=km * 1.25, limit=12)
    except Exception:  # noqa: BLE001
        landmarks = []

    # Rivers are water too — and they are LINES, so their grazing zones are
    # distance ribbons along the river, not rings around one point.
    from app.services import river_zones

    rivers: list[dict] = []
    ribbons: dict | None = None
    try:
        span = max([r["km"] for r in rings], default=0.0) or 25.0
        rivers = river_zones.nearby_rivers(anchor_lon, anchor_lat,
                                           radius_km=span * 1.25, limit=6)
        if main_id and eff_km:
            ribbons = river_zones.river_zone_ribbons(
                anchor_lon, anchor_lat, reach_km=eff_km,
                max_km=max(span, eff_km) * 1.05)
    except Exception:  # noqa: BLE001
        rivers, ribbons = [], None

    label = name or map_renderer._herder_place_label(lon, lat)
    return {
        "lang": lang,
        "title": text["title"], "tap": text["tap"],
        "you": text["you"] + (f" ({label})" if label else ""),
        "herder": {"lon": lon, "lat": lat},
        "options": options,
        "rings": rings,
        "landmarks": landmarks,
        "rivers": rivers,
        "ribbons": ribbons,
        "species": species,
        "interval": interval,
        "main_id": main_id,
        "overlay": overlay,
        "ortho": ortho,
        "eff_km": eff_km,
        "text": text,
    }


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def mapview_page(
    lat: float = Query(default=0.35, ge=-90, le=90),
    lon: float = Query(default=37.58, ge=-180, le=180),
    species: str = Query(default="camel", pattern="^(cattle|shoat|camel)$"),
    interval: str = Query(default="daily", pattern="^(daily|every_2_3_days)$"),
    id: str | None = Query(default=None, description="water source to show rings+pasture for"),
    numbered: str | None = Query(default=None,
                                 description="comma-separated water source uuids (numbered pins)"),
    lang: str = Query(default="swa", pattern="^(swa|eng)$"),
    name: str | None = Query(default=None, description="herder display label override"),
    focus: int = Query(default=0,
                       description="focus=1: zoom to the water point's rings/pasture"),
    graze: int = Query(default=0,
                       description="graze=1: tap-to-pin mode for the grazing ledger"),
    t: str | None = Query(default=None, description="graze token (see /grazing/pin)"),
) -> HTMLResponse:
    try:
        # Grazing-ledger mode. The token identifies the herder to /grazing/pin
        # WITHOUT a phone number ever appearing in a URL that gets forwarded.
        #
        # Coerced explicitly, because this function is also called directly (tests,
        # and any future caller) where FastAPI's Query defaults are still objects:
        # putting a Query into the payload explodes in json.dumps.
        graze_on = graze is True or (isinstance(graze, int) and graze > 0)
        # In graze mode the tap is how the herder records where the herd grazed, so
        # the page must not decide anything for him: no auto-focus there.
        data = _payload(lat, lon, species, interval, id, numbered, name, lang,
                        auto_focus=not graze_on)
        data["focus"] = 1 if focus else 0
        data["graze"] = {"on": graze_on,
                         "token": t if isinstance(t, str) else "",
                         "endpoint": "/grazing/pin"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"mapview failed: {e}") from e
    html = _PAGE_TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    return HTMLResponse(html)


@router.get("/api")
def mapview_api(
    lat: float = Query(default=0.35, ge=-90, le=90),
    lon: float = Query(default=37.58, ge=-180, le=180),
    species: str = Query(default="camel", pattern="^(cattle|shoat|camel)$"),
    interval: str = Query(default="daily", pattern="^(daily|every_2_3_days)$"),
    id: str | None = Query(default=None),
    lang: str = Query(default="swa", pattern="^(swa|eng)$"),
) -> dict:
    """JSON payload (rings/overlay for one water point) — used when the herder
    taps a pin so the map can switch without reloading the page."""
    data = _payload(lat, lon, species, interval, id, None, None, lang)
    data["focus"] = 1 if id else 0
    return data


_PAGE_TEMPLATE = r"""<!doctype html>
<html lang="sw"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Ramani</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
  /* Named-place labels (villages, markets, rivers) — always visible, on top. */
  .lm-label{background:rgba(255,255,255,.86);border:0;box-shadow:0 0 0 1px rgba(0,0,0,.15);
    border-radius:3px;font-size:11px;font-weight:700;color:#111;padding:0 3px;
    white-space:nowrap;text-shadow:0 1px 0 #fff}
  .lm-label:before{display:none !important}
</style>
</head><body>
<div id="map" style="position:fixed;top:0;bottom:0;left:0;right:0"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const D = __DATA__;
document.title = D.title;
const TXT = D.text || {};
const T = { legend: 'Alama za maji', rings: 'Upeo wa kufikia', dist: 'kutoka kwako',
            noName: 'Maji', you: 'Wewe hapa' };
const typeName = { river:'River/Mto', borehole:'Borehole', well:'Well/Kisima',
  spring:'Spring/Chemchemi', pan:'Pan/Bwawa', dam:'Dam/Bwawa', tap:'Tap/Mfereji' };
// Every water point is one blue: the popup label says which kind it is. Five marker
// colours used to fight the rings, the pasture layer and each other.
const WATER_HEX = '#2563eb';
const ringHex = { cattle:'#7c3aed', shoat:'#db2777', camel:'#ea580c' };
const pastureCols = { green:'#16a34a', dry:'#8a4d17', bare:'#78716c' };
// Herder-reported water status: blue = confirmed water now, amber = seasonal,
// red = reported dry/broken/gone, grey = never confirmed.
const statusCols = { functional:'#0ea5e9', flowing:'#0ea5e9', intermittent:'#f59e0b',
  dry:'#b91c1c', broken:'#b91c1c', not_found:'#b91c1c', unknown:'#6b7280' };
const statusName = { functional: TXT.s_ok, flowing: TXT.s_ok, intermittent: TXT.s_season,
  dry: TXT.s_dry, broken: TXT.s_broken, not_found: TXT.s_gone, unknown: TXT.s_unknown };
function pinColor(w) {
  const s = w.status && w.status !== 'unknown' ? statusCols[w.status] : null;
  // Status (blue = confirmed water today, red = dry/broken/gone, grey = unconfirmed)
  // wins; otherwise water is water, in one blue.
  return s || WATER_HEX;
}

// Zoom like a map app: he has to be able to walk right down to his own plot.
//
// THE SATELLITE'S REAL LIMIT IS 18, NOT 19 (measured, not guessed): at z19+ Esri serves
// a byte-identical placeholder — "Map data not yet available" — for this part of Isiolo.
// maxNativeZoom 18 makes Leaflet fetch the last REAL picture and stretch it for every
// deeper zoom, so he gets blurred ground instead of a blank tile saying no data.
const ESRI_NATIVE = 18;
const map = L.map('map', { zoomControl: true, attributionControl: true, maxZoom: 22 })
  .setView([D.herder.lat, D.herder.lon], 10);
const osmTiles = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, maxNativeZoom: 18,
  attribution: '&copy; OpenStreetMap contributors'});
const esriTiles = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/' +
  'World_Imagery/MapServer/tile/{z}/{y}/{x}', {
  maxZoom: 22, maxNativeZoom: ESRI_NATIVE,
  attribution: '&copy Esri, Maxar, Earthstar Geographics'});
// THE REAL GROUND COMES FIRST. A herder recognises a place from its picture, not from a
// street drawing, so the satellite is what loads — and the map stays one tap away in the
// switcher (below), with the pasture colour over the top of it.
esriTiles.addTo(map);
L.control.layers(
  { [TXT.satBase || 'Satellite']: esriTiles, [TXT.mapBase || 'Map']: osmTiles },
  null, { position: 'topright', collapsed: true }).addTo(map);
// A scale bar, because "precision" has to be readable in metres on the ground.
L.control.scale({ metric: true, imperial: false, position: 'bottomleft' }).addTo(map);
// ...and when the picture is stretched, say so. Blurred ground is still ground; a blank
// tile claiming "no map data" is worse than either.
const deepNote = document.createElement('div');
deepNote.id = 'deepnote';
deepNote.style.cssText = 'position:fixed;right:8px;bottom:26px;z-index:1000;' +
  'background:rgba(15,23,42,.85);color:#fff;border-radius:6px;padding:4px 7px;' +
  'font:600 11px/1.3 system-ui,sans-serif;display:none;max-width:46%';
deepNote.textContent = TXT.g_stretch ||
  'Satellite image stretched (deepest real detail ~0.6 m)';
document.body.appendChild(deepNote);
// A CLOSE-UP image (drone/aerial orthophoto) for this point, when one exists: same
// footprint as the pasture layer, so it can be swapped in at the depth where the
// satellite turns to blur. Absent image = stretched satellite, honestly labelled.
let orthoLayer = null;
if (D.ortho && D.ortho.url && D.ortho.bounds) {
  orthoLayer = L.imageOverlay(D.ortho.url, L.latLngBounds(D.ortho.bounds),
    { opacity: 1, interactive: false });
}
function applyDepthNotes() {
  const sat = map.hasLayer(esriTiles);
  const deep = sat && map.getZoom() > ESRI_NATIVE;
  const sharp = !!orthoLayer && deep;
  if (orthoLayer) {
    if (sharp && !map.hasLayer(orthoLayer)) map.addLayer(orthoLayer);
    else if (!sharp && map.hasLayer(orthoLayer)) map.removeLayer(orthoLayer);
  }
  deepNote.style.display = (deep || sharp) ? 'block' : 'none';
  deepNote.textContent = sharp
    ? (TXT.g_close || 'Close-up image of this point')
    : (TXT.g_stretch || 'Satellite image stretched (deepest real detail ~0.6 m)');
}
// Pasture colours stay semi-transparent so the base map stays readable.
map.on('baselayerchange', function () { applyOverlayStyle(); applyDepthNotes(); });
// Depth changes what is useful: the colour fades and then leaves, the names arrive,
// and past the imagery's real limit he is told the picture is stretched.
map.on('zoomend', function () {
  applyOverlayStyle(); applyLabels(); applyDepthNotes();
});

// Focus URL for a water point (tap a pin -> its rings + pasture).
function focusUrl(wid) {
  const p = new URLSearchParams({ lat: D.herder.lat, lon: D.herder.lon,
    species: D.species || 'camel', interval: D.interval || 'daily',
    lang: D.lang || 'swa', id: wid, focus: '1' });
  return '/mapview/?' + p.toString();
}


// Herder pin (blue).
const youIcon = L.divIcon({ html: '<div style="width:20px;height:20px;border-radius:50%;'+
  'background:#2563eb;border:3px solid #fff;box-shadow:0 0 0 3px rgba(37,99,235,.5)"></div>',
  className: '', iconSize: [26, 26], iconAnchor: [13, 13] });
L.marker([D.herder.lat, D.herder.lon], { icon: youIcon })
 .addTo(map).bindPopup('<b>' + D.you + '</b>');

let overlayLayer = null;
let overlayForced = false;   // he tapped "show pasture" while zoomed right in
let ringLayers = [];
const fitTargets = [L.latLng(D.herder.lat, D.herder.lon)];

// Satellite pasture overlay for the focused water point — framed to the water
// point's full ring stack (all species rings shown). Semi-transparent so the
// map underneath stays visible.
if (D.overlay && D.overlay.url && D.overlay.available) {
  overlayLayer = L.imageOverlay(D.overlay.url, L.latLngBounds(D.overlay.bounds),
    { opacity: 0.55, interactive: false }).addTo(map);
}

// THE COLOUR STEPS ASIDE WHEN HE ZOOMS IN. The pasture layer is 10 m pixels: once he
// is close enough to look for his own plot, one pixel fills the screen and the layer
// hides exactly the ground he came to see. So it fades with depth and leaves above z16
// — one tap on the toggle brings it straight back if he wants it.
const DEEP_ZOOM = 16;
function applyOverlayStyle() {
  if (!overlayLayer) return;
  const z = map.getZoom();
  const sat = map.hasLayer(esriTiles);
  if (z > 15 && !overlayForced) {
    if (map.hasLayer(overlayLayer)) map.removeLayer(overlayLayer);
    const btn = document.getElementById('ptoggle');
    if (btn) btn.textContent = TXT.off || 'Onyesha malisho';
    return;
  }
  if (!map.hasLayer(overlayLayer)) map.addLayer(overlayLayer);
  overlayLayer.setOpacity(z >= 14 ? (sat ? 0.4 : 0.35) : (sat ? 0.7 : 0.55));
}

// River grazing zones: distance RIBBONS along the river line (a river is long,
// so its zones follow the river instead of circling one point). Drawn under the
// rings, above the pasture colour.
// Grazing-zone limit ribbons: neutral white, because they are distance limits, not a
// category. (Green here used to collide with the pasture layer; amber with the camel
// ring.) 'critical' keeps the warning red — returning to water late IS an emergency.
const zoneCols = { comfortable: '#ffffff', far: '#ffffff', critical: '#ce2020' };
const ribbonNames = { comfortable: TXT.z_near || 'near water', far: TXT.z_edge || 'edge',
  critical: TXT.z_limit || 'far limit — return to water' };
if (D.ribbons && D.ribbons.bands) {
  D.ribbons.bands.slice().reverse().forEach(b => {
    const c = zoneCols[b.level] || '#64748b';
    L.geoJSON(b.geojson, { style: { color: c, weight: b.level === 'critical' ? 2.5 : 1.5,
      dashArray: b.level === 'comfortable' ? null : '6 5',
      fillColor: c, fillOpacity: b.level === 'critical' ? 0.05 : 0.07 } }).addTo(map);
  });
}

// The rivers themselves, named (they are water points a herd can drink from).
(D.rivers || []).forEach(rv => {
  L.geoJSON(rv.geojson, { style: { color: '#1d4ed8', weight: 3, opacity: 0.9 } })
   .bindTooltip(rv.name + ' (' + (TXT.k_river || 'river') + ' · ' + rv.dist_km + ' km)',
                { permanent: true, direction: 'center', className: 'lm-label' })
   .addTo(map);
});
const lmKind = { city:'#111827', town:'#111827', village:'#374151', market:'#7c2d12',
  river:'#1d4ed8', hamlet:'#4b5563', peak:'#78350f' };
const lmSeenKinds = {};
(D.landmarks || []).forEach(l => {
  const c = lmKind[l.kind] || '#374151';
  lmSeenKinds[l.kind] = true;
  const icon = L.divIcon({ className: '', iconSize: [8, 8], iconAnchor: [4, 4],
    html: '<div style="width:8px;height:8px;border-radius:50%;background:' + c +
          ';border:1.5px solid #fff;box-shadow:0 0 2px rgba(0,0,0,.6)"></div>' });
  L.marker([l.lat, l.lon], { icon: icon })
   .bindTooltip(l.name + (l.dist_km != null ? ' · ' + l.dist_km + ' km' : ''),
                { permanent: true, direction: 'right', offset: [7, 0],
                  className: 'lm-label' })
   .addTo(map);
});

// Species rings (scaled to the watering interval) for the focused water point.
function drawRings(rings) {
  (ringLayers || []).forEach(l => map.removeLayer(l));
  ringLayers = [];
  (rings || []).forEach(r => {
    const layer = L.geoJSON(r.geojson, { style: { color: ringHex[r.species] || '#64748b',
      weight: 2, dashArray: '5 5', fillOpacity: 0.05 } }).addTo(map);
    ringLayers.push(layer);
  });
}
drawRings(D.rings);

// bbox of the rings (for focusing when no pasture overlay is available)
function ringBounds() {
  let s = 90, w = 180, n = -90, e = -180, any = false;
  (D.rings || []).forEach(r => {
    const geom = r.geojson && r.geojson.geometry;
    if (!geom) return;
    const polys = geom.type === 'Polygon' ? [geom.coordinates] : geom.coordinates || [];
    polys.forEach(poly => poly.forEach(ring => ring.forEach(p => {
      s = Math.min(s, p[1]); w = Math.min(w, p[0]);
      n = Math.max(n, p[1]); e = Math.max(e, p[0]); any = true;
    })));
  });
  return any ? [[s, w], [n, e]] : null;
}

const seenTypes = {};
const seenStatus = {};
// Names appear when he is close enough for them to help, and disappear when he zooms
// out (otherwise the map becomes a wall of text). Rivers and landmarks keep their
// always-on labels: they are few, long and used for orientation from far away.
const NAME_ZOOM = 12;
const nameLayers = [];
function addName(layer, text) {
  nameLayers.push({layer: layer, text: text});
}
function applyLabels() {
  const show = map.getZoom() >= NAME_ZOOM;
  nameLayers.forEach(function (it) {
    const bound = it.layer.getTooltip();
    if (show && !bound) {
      it.layer.bindTooltip(it.text, {permanent: true, direction: 'right',
        offset: [9, 0], className: 'lm-label'}).openTooltip();
    } else if (!show && bound) {
      it.layer.closeTooltip();
      it.layer.unbindTooltip();
    }
  });
}
(D.options || []).forEach(w => {
  const c = pinColor(w);
  seenTypes[w.water_type || '?'] = true;
  seenStatus[w.status || 'unknown'] = true;
  const numHtml = w.num ? '<div style="position:absolute;top:-7px;left:-7px;width:26px;height:26px;' +
    'background:' + c + ';border:2px solid #fff;border-radius:50%;font-weight:800;color:#fff;' +
    'font-size:13px;display:flex;align-items:center;justify-content:center;box-shadow:0 1px 3px rgba(0,0,0,.5)">' +
    w.num + '</div>' : '';
  const icon = L.divIcon({ html: '<div style="width:14px;height:14px;border-radius:50%;' +
    'background:' + c + ';border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.4)"></div>' +
    numHtml, className: '', iconSize: [28, 28], iconAnchor: [14, 14] });
  const type = typeName[w.water_type] || '';
  const d = w.dist_km != null ? ' ~' + w.dist_km.toFixed(1) + ' km' : '';
  const label = (w.num ? w.num + '. ' : '') + (w.name || T.noName);
  const st = w.status && w.status !== 'unknown'
    ? '<br><b style="color:' + pinColor(w) + '">' + (statusName[w.status] || w.status) +
      (w.status_age_days != null ? ' (siku ' + w.status_age_days + ')' : '') + '</b>'
    : (w.needs_check ? '<br><span style="color:#6b7280">' + (TXT.s_unknown || 'not confirmed') + '</span>' : '');
  const marker = L.marker([w.lat, w.lon], { icon }).addTo(map)
   .bindPopup('<b>' + label + '</b>' + (type ? '<br>' + type : '') + st + '<br>' + d + ' ' + T.dist +
     '<br><a href="' + focusUrl(w.id) + '" style="font-weight:700">' +
     (TXT.show || 'Show rings + pasture') + '</a>');
  // His own name for the place, worn on the map itself once he is close enough for a
  // name to mean something. Zoomed out it would be a wall of text; zoomed in it is how
  // he recognises where he is standing.
  addName(marker, label + d);
  fitTargets.push(L.latLng(w.lat, w.lon));
});


// Legend + pasture summary + pasture on/off toggle.
const Legend = L.Control.extend({
  onAdd: function () {
    const el = L.DomUtil.create('div', 'leaflet-control');
    const p = D.overlay || {};
    const frac = p.frac || {};
    let html = '<div style="background:#fff;border-radius:8px;padding:8px 10px;' +
      'box-shadow:0 1px 5px rgba(0,0,0,.3);font-size:13px;max-width:255px">';
    if (p.url && p.available) {
      html += '<b>' + (TXT.pasture || 'Pasture') + '</b>' +
        '<div><span style="display:inline-block;width:11px;height:11px;border-radius:2px;' +
        'background:' + pastureCols.green + ';margin-right:6px"></span>' + (TXT.grass||'green = grass') +
        (frac.green != null ? ' <b>' + frac.green + '%</b>' : '') + '</div>' +
        '<div><span style="display:inline-block;width:11px;height:11px;border-radius:2px;' +
        'background:' + pastureCols.dry + ';margin-right:6px"></span>' + (TXT.dry||'olive = dry forage') +
        (frac.dry != null ? ' <b>' + frac.dry + '%</b>' : '') + '</div>' +
        '<div><span style="display:inline-block;width:11px;height:11px;border-radius:2px;' +
        'background:' + pastureCols.bare + ';margin-right:6px"></span>' + (TXT.bare||'grey = bare') +
        (frac.bare != null ? ' <b>' + frac.bare + '%</b>' : '') + '</div>';
      if (p.usable_pct != null) {
        html += '<div style="margin-top:3px;font-weight:700">' + (TXT.summary||'Pasture now') +
          ': <b>' + p.usable_pct + '%</b></div>';
      }
      // BLANK PIXELS ARE A DECISION, NOT A FAULT. We leave a pixel uncoloured when the
      // satellite cannot be read there (cloud, shadow, no clean pass) or when it is
      // outside his grazing reach. Left unexplained, holes read as "the map is broken";
      // named — and numbered — they read as what they are. The share is of HIS ring, so
      // a few gaps look like a few gaps and a clouded week looks like a clouded week.
      let blankTxt = (TXT.no_read || 'Uncoloured patches');
      if (p.blank_pct != null && p.blank_pct > 0) {
        blankTxt += ': <b>' + p.blank_pct + '%</b> ' +
          (TXT.no_read_why || 'of your reach we could not read');
      }
      html += '<div style="margin-top:4px;color:#475569;line-height:1.35">' + blankTxt +
        ' — ' + (TXT.no_guess || 'we do not guess (cloud, shadow, no clean pass)') +
        '</div>' +
        '<div style="color:#475569;line-height:1.35">' +
        (TXT.ring_clip || 'Colour appears inside the dashed reach line only.') + '</div>';
      html += '<div style="margin-top:3px"><button id="ptoggle" style="font-size:12px">' +
        (TXT.on || 'Hide pasture') + '</button></div>';
    } else if (p.url) {
      html += '<div style="color:#92400e">' + (TXT.preparing || 'Pasture layer being prepared') + '</div>';
    } else {
      html += '<div style="color:#334155">' + (TXT.tap || 'Tap a water pin') + '</div>';
    }
    const wrows = Object.keys(seenTypes).map(k =>
      '<div style="line-height:1.5"><span style="display:inline-block;width:11px;height:11px;' +
      'border-radius:50%;background:' + WATER_HEX + ';margin-right:6px"></span>' +
      (typeName[k] || k) + '</div>').join('');
    if (wrows) html += '<b>' + T.legend + '</b>' + wrows;
    // Herder-reported water status — the most actionable line on the map.
    const stRows = Object.keys(seenStatus).map(k =>
      '<div style="line-height:1.5"><span style="display:inline-block;width:11px;height:11px;' +
      'border-radius:50%;background:' + (statusCols[k] || '#6b7280') + ';margin-right:6px"></span>' +
      (statusName[k] || k) + '</div>').join('');
    if (stRows) html += '<b>' + (TXT.water_status || 'Water status (from herders)') + '</b>' + stRows;
    // Named places drawn on the map (towns, villages, markets, rivers, hills).
    const lmName = { city: TXT.k_city, town: TXT.k_city, village: TXT.k_village,
      hamlet: TXT.k_village, market: TXT.k_market, river: TXT.k_river, peak: TXT.k_peak };
    const lmRows = Object.keys(lmSeenKinds).map(k =>
      '<div style="line-height:1.5"><span style="display:inline-block;width:11px;height:11px;' +
      'border-radius:50%;background:' + (lmKind[k] || '#374151') + ';margin-right:6px"></span>' +
      (lmName[k] || k) + '</div>').join('');
    if (lmRows) html += '<b>' + (TXT.places || 'Named places') + '</b>' + lmRows;
    // Rivers + their distance ribbons (zones follow the river line).
    if (D.rivers && D.rivers.length) {
      let rv = '<b>' + (TXT.rivers || 'Rivers — zones follow the river') + '</b>' +
        '<div style="line-height:1.5"><span style="display:inline-block;width:14px;height:3px;' +
        'background:#1d4ed8;margin-right:6px;vertical-align:middle"></span>' +
        (TXT.k_river || 'river') + ': ' +
        D.rivers.slice(0, 3).map(r => r.name + ' ' + r.dist_km + ' km').join(', ') + '</div>';
      if (D.ribbons && D.ribbons.bands) {
        rv += D.ribbons.bands.map(b =>
          '<div style="line-height:1.5"><span style="display:inline-block;width:11px;height:11px;' +
          'border-radius:2px;background:' + (zoneCols[b.level] || '#64748b') +
          ';margin-right:6px"></span>' + (ribbonNames[b.level] || b.level) +
          ' <b>&lt; ' + b.km + ' km</b></div>').join('');
      }
      html += rv;
    }
    const spName = { cattle: TXT.cattle || 'cattle', shoat: TXT.shoat || 'shoat',
      camel: TXT.camel || 'camel' };
    const rows = (D.rings || []).map(r =>
      '<div style="line-height:1.5"><span style="display:inline-block;width:10px;height:10px;' +
      'border:2px solid ' + (ringHex[r.species] || '#64748b') + ';margin-right:6px"></span>' +
      (spName[r.species] || r.species) + ' ' + r.km + ' km</div>').join('');
    if (rows) html += '<b>' + T.rings + '</b>' + rows;
    html += '</div>';
    el.innerHTML = html;
    return el;
  }
});
map.addControl(new Legend({ position: 'bottomleft' }));

// Toggle pasture layer on/off.
document.body.addEventListener('click', function (ev) {
  if (ev.target && ev.target.id === 'ptoggle' && overlayLayer) {
    if (map.hasLayer(overlayLayer)) {
      map.removeLayer(overlayLayer);
      overlayForced = false;
      ev.target.textContent = TXT.off || 'Show pasture';
    } else {
      // He asked for the colour at a depth where it normally steps aside: respect him,
      // and hand the decision back to the zoom as soon as he zooms out again.
      map.addLayer(overlayLayer);
      overlayForced = map.getZoom() > 15;
      ev.target.textContent = TXT.on || 'Hide pasture';
    }
    applyOverlayStyle();
  }
});

// --- grazing ledger: tap the spot they grazed today -------------------------
// Only in graze mode (how the link from WhatsApp arrives). The tap goes to
// /grazing/pin with a short-lived token — never a phone number — and the ANSWER
// comes back on WhatsApp, because the phone is where a herder keeps his record.
if (D.graze && D.graze.on && D.graze.token) {
  // Tap sets the CENTRE of the patch; he then chooses how big it was, so the hectares we
  // record are the hectares he means. It is drawn with L.circle, in METRES: a fixed
  // pixel dot claims a different size on the ground at every zoom level, which is a lie
  // that gets worse the closer he looks.
  const bar = document.createElement('div');
  // Neutral chrome, NOT green: on this map green means pasture quality and
  // nothing else (scripts/test_map_palette.py enforces it). An instruction banner
  // in green would tell a herder "this is good grass" while asking him for input.
  bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:1000;background:#0f172a;' +
    'color:#fff;font:700 14px/1.35 system-ui,sans-serif;padding:10px 12px;text-align:center';
  bar.textContent = TXT.g_hint || 'Tap where they grazed today';
  document.body.appendChild(bar);

  const panel = document.createElement('div');
  panel.style.cssText = 'position:fixed;bottom:52px;left:8px;right:8px;z-index:1000;' +
    'background:#fff;color:#0f172a;font:600 13px/1.4 system-ui,sans-serif;' +
    'border-radius:10px;padding:8px 10px;box-shadow:0 2px 10px rgba(0,0,0,.35)';
  document.body.appendChild(panel);

  const SIZES = [50, 100, 250, 500];   // metres of radius: a plot, a field, a grazing area
  let radius = 100, spot = null, circle = null, centre = null, busy = false;

  function refresh() {
    if (!spot) {
      panel.textContent = TXT.g_zoom ||
        'Zoom in until you see your area, then tap its centre.';
      return;
    }
    const ha = (Math.PI * radius * radius) / 10000;
    panel.innerHTML = '<div>' + (TXT.g_size || 'Area size') + ': ' +
      SIZES.map(function (r) {
        return '<button data-r="' + r + '" style="margin:2px;padding:3px 8px;' +
          'border-radius:6px;border:1px solid #94a3b8;background:' +
          (r === radius ? '#0f172a' : '#fff') + ';color:' +
          (r === radius ? '#fff' : '#0f172a') + '">' + r + ' m</button>';
      }).join('') + '</div>' +
      '<div style="margin-top:4px">' + (2 * radius) + ' m ' +
      (TXT.g_across || 'across') + ' · ' + ha.toFixed(1) + ' ' + (TXT.g_ha || 'hectares') +
      '</div>' +
      '<div style="margin-top:2px;color:#475569">' + spot.lat.toFixed(5) + ', ' +
      spot.lng.toFixed(5) + '</div>' +
      '<div style="margin-top:2px;color:#64748b;font-weight:400">' +
      (TXT.g_data || '') + '</div>' +
      '<button id="gsend" style="margin-top:6px;width:100%;padding:8px;border:0;' +
      'border-radius:8px;background:#0f172a;color:#fff;font:700 14px system-ui">' +
      (TXT.g_send || 'Send this area') + '</button>';
  }

  map.on('click', function (ev) {
    spot = ev.latlng;
    if (circle) map.removeLayer(circle);
    if (centre) map.removeLayer(centre);
    circle = L.circle(spot, {radius: radius, color: '#0f172a', weight: 2,
      fillColor: '#ffffff', fillOpacity: 0.25}).addTo(map);
    centre = L.circleMarker(spot, {radius: 4, color: '#0f172a', weight: 2,
      fillColor: '#ffffff', fillOpacity: 1}).addTo(map);
    refresh();
  });

  panel.addEventListener('click', function (ev) {
    const t = ev.target;
    if (!t) return;
    if (t.dataset && t.dataset.r) {
      radius = parseInt(t.dataset.r, 10) || radius;
      if (circle) circle.setRadius(radius);
      refresh();
      return;
    }
    if (t.id === 'gsend' && spot && !busy) {
      busy = true;
      bar.textContent = TXT.g_sending || 'Sending...';
      fetch(D.graze.endpoint, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        // point_id is the raster this page PAINTED: the answer must be read from the
        // same satellite file he was looking at, not from whichever point happens to
        // be nearest the tap (a brand-new point has no raster yet at all).
        // radius_m is the patch he drew, in metres.
        body: JSON.stringify({lat: spot.lat, lon: spot.lng, radius_m: radius,
          token: D.graze.token, point_id: D.main_id || null})
      }).then(r => r.ok ? r.json() : Promise.reject(r.status))
        .then(() => { bar.textContent = TXT.g_sent || 'Sent'; panel.innerHTML = ''; })
        .catch(() => { bar.textContent = TXT.g_fail || 'Failed'; })
        .finally(() => { busy = false; });
    }
  });
  refresh();
}

// --- "Where am I?" ----------------------------------------------------------
// The one thing a map app gives a person that we can honestly give him too: his own
// dot on the ground. Street View does not exist out here (Google has almost no
// coverage in Isiolo, and the imagery is licensed), so recognition has to come from
// his position + the names he gave us + the ground itself.
const whereBtn = document.createElement('button');
whereBtn.id = 'wherebtn';
whereBtn.style.cssText = 'position:fixed;left:10px;top:84px;z-index:1000;' +
  'background:#0f172a;color:#fff;border:0;border-radius:8px;padding:8px 10px;' +
  'font:700 13px system-ui,sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.4)';
whereBtn.textContent = TXT.g_where || 'Where am I?';
document.body.appendChild(whereBtn);
const whereBox = document.createElement('div');
whereBox.style.cssText = 'position:fixed;left:8px;bottom:26px;z-index:1000;background:#fff;' +
  'color:#0f172a;border-radius:8px;padding:6px 8px;font:600 12px/1.35 system-ui,sans-serif;' +
  'box-shadow:0 1px 6px rgba(0,0,0,.3);display:none';
document.body.appendChild(whereBox);

const COMPASS = ['kaskazini', 'kaskazini-mashariki', 'mashariki', 'kusini-mashariki',
                 'kusini', 'kusini-magharibi', 'magharibi', 'kaskazini-magharibi'];
let youAcc = null, youDot = null, lastFix = null;

function bearingWord(from, to) {
  const dLon = (to[1] - from[1]) * Math.cos((from[0] + to[0]) / 2 * Math.PI / 180);
  const deg = (Math.atan2(dLon, to[0] - from[0]) * 180 / Math.PI + 360) % 360;
  return COMPASS[Math.round(deg / 45) % 8];
}

function showFix(latlng, accuracy) {
  lastFix = latlng;
  if (youAcc) map.removeLayer(youAcc);
  if (youDot) map.removeLayer(youDot);
  // The halo is geometry, so it claims no hue; the dot is the same blue as the "wewe
  // hapa" pin, so the two can never disagree about which dot is him.
  youAcc = L.circle(latlng, {radius: accuracy, color: '#0f172a', weight: 1,
    dashArray: '4 4', fillColor: '#0f172a', fillOpacity: 0.05}).addTo(map);
  youDot = L.circleMarker(latlng, {radius: 7, color: '#0f172a', weight: 3,
    fillColor: '#2563eb', fillOpacity: 1}).addTo(map);
  youDot.bindTooltip((TXT.g_here || 'You are here') + ' ±' + Math.round(accuracy) + ' m');
  const target = (D.options || []).filter(function (w) { return w.id === D.main_id; })[0];
  const to = target ? [target.lat, target.lon] : [D.herder.lat, D.herder.lon];
  const m = map.distance(latlng, to);
  const dist = m >= 950 ? (m / 1000).toFixed(1) + ' km' : Math.round(m) + ' m';
  const mins = Math.max(1, Math.round((m / 1000) / 5 * 60));   // ~5 km/h walking
  whereBox.innerHTML = dist + ' ' + (TXT.g_from || 'from') + ' ' +
    (target ? (target.name || T.noName) : (D.you || '')) + ' · ' +
    bearingWord(latlng, to) + '<br>' + (TXT.g_walk || 'walk about') + ' ' + mins + ' dakika';
  whereBox.style.display = 'block';
}

map.on('locationfound', function (e) { showFix(e.latlng, e.accuracy || 30); });
map.on('locationerror', function () {
  // Honest failure: in-app browsers often refuse location. Say so plainly rather than
  // leaving him tapping a button that does nothing.
  whereBtn.textContent = TXT.g_where_no || 'This phone did not allow location';
  whereBtn.disabled = true;
  whereBtn.style.opacity = '0.7';
});
document.body.addEventListener('click', function (ev) {
  if (ev.target && ev.target.id === 'wherebtn') {
    if (lastFix) {
      map.setView(lastFix, Math.max(map.getZoom(), 16));
      showFix(lastFix, 30);
      return;
    }
    map.locate({setView: true, maxZoom: 17, enableHighAccuracy: true, timeout: 10000});
  }
});

// Zoom so everyone is on screen; on a focused water point, fit its rings
// (pasture colour always stays inside the widest ring, so this shows it all).
let boundsToFit = null;
const rb = ringBounds();
if (rb) {
  boundsToFit = L.latLngBounds(rb);
} else if (D.overlay && D.overlay.available) {
  boundsToFit = L.latLngBounds(D.overlay.bounds);
}
if (!boundsToFit && fitTargets.length > 1) boundsToFit = L.latLngBounds(fitTargets);
if (boundsToFit) map.fitBounds(boundsToFit, { padding: [45, 45], maxZoom: 13 });
// Apply the depth rules once on load: fitBounds above may have landed on a shallow zoom.
applyOverlayStyle();
applyLabels();
applyDepthNotes();
</script>
</body></html>"""

