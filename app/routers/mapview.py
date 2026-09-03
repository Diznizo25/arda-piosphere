"""Public interactive map page (Google-Maps-style).

`GET /mapview/` returns a mobile, zoomable Leaflet map (OpenStreetMap tiles)
showing:
  * the herder's blue "Wewe hapa (place)" pin
  * every nearby water point as a type-coloured pin (numbered when a list was
    shared) — TAPPING a pin opens THAT water point's species rings + satellite
    pasture layer
  * when `id=` is given (a reachable/confirmed water point), its species rings
    are drawn immediately (scaled to the herder's watering interval) with the
    transparent pasture layer + a quantity/quality summary in the legend

The link is shared from WhatsApp after maps/advisories.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.services import map_renderer, water_reach, water_sources

router = APIRouter(prefix="/mapview", tags=["mapview"])

_RING_HEX = {"cattle": "#3b82f6", "shoat": "#10b981", "camel": "#f97316"}


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
            "mapBase": "Map", "satBase": "Satellite",
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
        "mapBase": "Ramani", "satBase": "Satellite",
    }


def _scale_geojson_ring(geojson: dict, frac: float) -> dict:
    """Scale a ring polygon toward its own centre (effective reach at draw time)."""

    def scale_coords(coords):
        xs = [p[0] for p in coords]
        ys = [p[1] for p in coords]
        cx = (min(xs) + max(xs)) / 2.0
        cy = (min(ys) + max(ys)) / 2.0
        return [[cx + (x - cx) * frac, cy + (y - cy) * frac] for x, y in coords]

    g = geojson.get("geometry", {})
    if g.get("type") == "Polygon":
        g = {**g, "coordinates": [scale_coords(r) for r in g["coordinates"]]}
    elif g.get("type") == "MultiPolygon":
        g = {**g, "coordinates": [[scale_coords(r) for r in poly]
                                  for poly in g["coordinates"]]}
    return {**geojson, "geometry": g}

def _payload(lat: float, lon: float, species: str, interval: str,
             water_id: str | None, numbered: str | None, name: str | None,
             lang: str) -> dict:
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
                            "num": None, "dist_km": n["distance_km"]})

    # Rings + pasture ONLY for the specific water point requested (a reachable/
    # confirmed point). The pin-based page (no id) shows options and lets the
    # herder TAP a pin to load that point's rings/pasture.
    main_id = water_id if (water_id and water_id in by_id) else None
    rings: list[dict] = []
    eff_km: float | None = None
    overlay: dict | None = None
    if main_id:
        try:
            from app.config import get_species_rings

            rings_cfg = get_species_rings()
            eff_km = rings_cfg.effective_radius_km(species, interval)
            zones = water_sources.zones_for_water_source(main_id)
            for z in zones:
                gj = json.loads(z["geojson"])
                km = float(z["radius_km"])
                if z["species"] == species and km > 0:
                    gj = _scale_geojson_ring(gj, eff_km / km)
                    km = eff_km
                rings.append({"species": z["species"], "km": km,
                              "color": _RING_HEX.get(z["species"], "#64748b"),
                              "geojson": gj})
            status = map_renderer.pasture_overlay_status(main_id, species, interval)
            if status:
                from app.config import get_settings

                base = get_settings().app_public_base_url.rstrip("/")
                overlay = {
                    "url": (f"{base}/map/{main_id}/pasture.png?species={species}"
                            f"&interval={interval}&v=8"),
                    "bounds": status["bounds"],
                    "available": status["available"],
                    "usable_pct": status["usable_pct"],
                    "frac": status["frac"],
                }
        except Exception:  # noqa: BLE001
            rings, overlay = [], None

    label = name or map_renderer._herder_place_label(lon, lat)
    return {
        "lang": lang,
        "title": text["title"], "tap": text["tap"],
        "you": text["you"] + (f" ({label})" if label else ""),
        "herder": {"lon": lon, "lat": lat},
        "options": options,
        "rings": rings,
        "species": species,
        "interval": interval,
        "main_id": main_id,
        "overlay": overlay,
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
) -> HTMLResponse:
    try:
        data = _payload(lat, lon, species, interval, id, numbered, name, lang)
        data["focus"] = 1 if focus else 0
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
const typeColor = { river:'#2563eb', borehole:'#ea580c', well:'#059669',
  spring:'#16a34a', pan:'#06b6d4', dam:'#0891b2', lake:'#0891b2', tap:'#9333ea' };
const ringHex = { cattle:'#3b82f6', shoat:'#10b981', camel:'#f97316' };
const pastureCols = { green:'#22c55e', dry:'#96602d', bare:'#dc2626', unclear:'#f59e0b' };

const map = L.map('map', { zoomControl: true, attributionControl: true })
  .setView([D.herder.lat, D.herder.lon], 10);
const osmTiles = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'}).addTo(map);
const esriTiles = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/' +
  'World_Imagery/MapServer/tile/{z}/{y}/{x}', {
  maxZoom: 19,
  attribution: '&copy Esri, Maxar, Earthstar Geographics'});
// Base-map switch: 'Map' (street names) or 'Satellite' (real photos).
L.control.layers(
  { [TXT.mapBase || 'Map']: osmTiles, [TXT.satBase || 'Satellite']: esriTiles },
  null, { position: 'topright', collapsed: true }).addTo(map);
// On satellite base the pasture is blended so real ground cover shows through.
map.on('baselayerchange', function (e) {
  if (overlayLayer) overlayLayer.setOpacity(e.layer === esriTiles ? 0.75 : 0.6);
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
let ringLayers = [];
const fitTargets = [L.latLng(D.herder.lat, D.herder.lon)];

// Satellite pasture overlay for the focused water point — framed to the water
// point's full ring stack (all species rings shown). Semi-transparent so the
// map underneath stays visible.
if (D.overlay && D.overlay.url && D.overlay.available) {
  overlayLayer = L.imageOverlay(D.overlay.url, L.latLngBounds(D.overlay.bounds),
    { opacity: 0.6, interactive: false }).addTo(map);
  // Bring the pasture area into the default view so it is seen right away.
  fitTargets.push(L.latLngBounds(D.overlay.bounds));
}

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
(D.options || []).forEach(w => {
  const c = typeColor[w.water_type] || '#0f766e';
  seenTypes[w.water_type || '?'] = true;
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
  L.marker([w.lat, w.lon], { icon }).addTo(map)
   .bindPopup('<b>' + label + '</b>' + (type ? '<br>' + type : '') + '<br>' + d + ' ' + T.dist +
     '<br><a href="' + focusUrl(w.id) + '" style="font-weight:700">' +
     (TXT.show || 'Show rings + pasture') + '</a>');
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
        'background:' + pastureCols.bare + ';margin-right:6px"></span>' + (TXT.bare||'red = bare') +
        (frac.bare != null ? ' <b>' + frac.bare + '%</b>' : '') + '</div>';
      if (p.usable_pct != null) {
        html += '<div style="margin-top:3px;font-weight:700">' + (TXT.summary||'Pasture now') +
          ': <b>' + p.usable_pct + '%</b></div>';
      }
      html += '<div style="margin-top:3px"><button id="ptoggle" style="font-size:12px">' +
        (TXT.on || 'Hide pasture') + '</button></div>';
    } else if (p.url) {
      html += '<div style="color:#92400e">' + (TXT.preparing || 'Pasture layer being prepared') + '</div>';
    } else {
      html += '<div style="color:#065f46">' + (TXT.tap || 'Tap a water pin') + '</div>';
    }
    const wrows = Object.keys(seenTypes).map(k =>
      '<div style="line-height:1.6"><span style="display:inline-block;width:11px;height:11px;' +
      'border-radius:50%;background:' + (typeColor[k] || '#0f766e') + ';margin-right:6px"></span>' +
      (typeName[k] || k) + '</div>').join('');
    if (wrows) html += '<b>' + T.legend + '</b>' + wrows;
    const rows = (D.rings || []).map(r =>
      '<div style="line-height:1.6"><span style="display:inline-block;width:10px;height:10px;' +
      'border:2px solid ' + (ringHex[r.species] || '#64748b') + ';margin-right:6px"></span>' +
      r.species + ' ' + r.km + ' km</div>').join('');
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
      ev.target.textContent = TXT.off || 'Show pasture';
    } else {
      map.addLayer(overlayLayer);
      ev.target.textContent = TXT.on || 'Hide pasture';
    }
  }
});

// Zoom so everyone is on screen; on a focused water point, fit its pasture/rings.
let boundsToFit = null;
if (D.focus === 1) {
  if (D.overlay && D.overlay.available) {
    boundsToFit = L.latLngBounds(D.overlay.bounds);
  } else {
    const rb = ringBounds();
    if (rb) boundsToFit = L.latLngBounds(rb);
  }
}
if (!boundsToFit && fitTargets.length > 1) boundsToFit = L.latLngBounds(fitTargets);
if (boundsToFit) map.fitBounds(boundsToFit, { padding: [45, 45], maxZoom: 13 });
</script>
</body></html>"""

