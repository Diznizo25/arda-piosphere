"""Public interactive map page (Google-Maps-style).

WhatsApp static PNGs can't be zoomed. `GET /mapview/` returns a mobile,
zoomable Leaflet map (OpenStreetMap tiles) centred on the herder with:

  * their blue "Wewe hapa (place)" marker
  * every nearby water point as a type-coloured pin with its LOCAL name
    (or "Kisima karibu na <village>" when unnamed) + distance
  * numbered pins 1..N matching the WhatsApp confirmation list (optional)
  * the species piosphere rings for the water source (legend)

The link is sent inside WhatsApp captions so a herder can "tap to open and
zoom" — the same answer to 'where am I / where is the water' as Google Maps,
with our data on top.
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
            "title": "Live map — your water and grazing area",
            "you": "You are here",
            "tap": "Tap a pin for its name. Pinch to zoom in/out.",
        }
    return {
        "title": "Ramani hai — eneo lako la maji na malisho",
        "you": "Wewe hapa",
        "tap": "Bonyeza alama kujua jina. Piga zoom kwa vidole viwili.",
    }


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def mapview_page(
    lat: float = Query(default=0.35, ge=-90, le=90),
    lon: float = Query(default=37.58, ge=-180, le=180),
    species: str = Query(default="camel", pattern="^(cattle|shoat|camel)$"),
    id: str | None = Query(default=None, description="main water source (rings)"),
    numbered: str | None = Query(default=None,
                                 description="comma-separated water source uuids"),
    lang: str = Query(default="swa", pattern="^(swa|eng)$"),
    name: str | None = Query(default=None, description="herder display label override"),
) -> HTMLResponse:
    text = _t(lang)
    try:
        numbered_ids = [u for u in (numbered or "").split(",") if u]
        by_id: dict[str, water_sources.WaterSource] = {}
        try:
            by_id = {w.id: w for w in water_sources.list_water_sources()}
        except Exception:  # noqa: BLE001
            by_id = {}
        # Nearby options: numbered list when given, else nearest 10.
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
        # Species rings for the main water source.
        rings: list[dict] = []
        ring_source_id = id if (id and id in by_id) else (options[0]["id"] if options else None)
        if ring_source_id:
            try:
                zones = water_sources.zones_for_water_source(ring_source_id)
                rings = [{"species": z["species"], "km": z["radius_km"],
                          "color": _RING_HEX.get(z["species"], "#64748b"),
                          "geojson": json.loads(z["geojson"])} for z in zones]
            except Exception:  # noqa: BLE001
                rings = []
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"mapview failed: {e}") from e

    label = name or map_renderer._herder_place_label(lon, lat)
    data = {
        "lang": lang,
        "title": text["title"], "tap": text["tap"],
        "you": text["you"] + (f" ({label})" if label else ""),
        "herder": {"lon": lon, "lat": lat},
        "options": options,
        "rings": rings,
        "species": species,
    }
    html = _PAGE_TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    return HTMLResponse(html)




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
const T = D.lang === 'eng' ? { you:'You are here', dist:'away', legend:'Water pins',
  rings:'Grazing reach', noName:'Water' }
  : { you:'Wewe hapa', dist:'kutoka kwako', legend:'Alama za maji',
      rings:'Upeo wa kufikia', noName:'Maji' };
const typeName = { river:'River/Mto', borehole:'Borehole', well:'Well/Kisima',
  spring:'Spring/Chemchemi', pan:'Pan/Bwawa', dam:'Dam/Bwawa', tap:'Tap/Mfereji' };
const typeColor = { river:'#2563eb', borehole:'#ea580c', well:'#059669',
  spring:'#16a34a', pan:'#06b6d4', dam:'#0891b2', lake:'#0891b2', tap:'#9333ea' };
const ringHex = { cattle:'#3b82f6', shoat:'#10b981', camel:'#f97316' };

const map = L.map('map', { zoomControl: true, attributionControl: true })
  .setView([D.herder.lat, D.herder.lon], 11);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'}).addTo(map);


// Herder pin (blue).
const youIcon = L.divIcon({ html: '<div style="width:20px;height:20px;border-radius:50%;'+
  'background:#2563eb;border:3px solid #fff;box-shadow:0 0 0 3px rgba(37,99,235,.5)"></div>',
  className: '', iconSize: [26, 26], iconAnchor: [13, 13] });
L.marker([D.herder.lat, D.herder.lon], { icon: youIcon })
 .addTo(map).bindPopup('<b>' + D.you + '</b>');

const latLngs = [[D.herder.lat, D.herder.lon]];
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
   .bindPopup('<b>' + label + '</b>' + (type ? '<br>' + type : '') + '<br>' + d + ' ' + T.dist);
  latLngs.push([w.lat, w.lon]);
});

// Piosphere rings.
(D.rings || []).forEach(r => {
  L.geoJSON(r.geojson, { style: { color: ringHex[r.species] || '#64748b',
    weight: 2, dashArray: '5 5', fillOpacity: 0.05 } }).addTo(map);
});

// Legend control.
const Legend = L.Control.extend({
  onAdd: function () {
    const el = L.DomUtil.create('div', 'leaflet-control');
    let rows = (D.rings || []).map(r =>
      '<div style="line-height:1.6"><span style="display:inline-block;width:10px;height:10px;' +
      'border:2px solid ' + (ringHex[r.species] || '#64748b') + ';margin-right:6px"></span>' +
      r.species + ' ' + r.km + ' km</div>').join('');
    const wrows = Object.keys(seenTypes).map(k =>
      '<div style="line-height:1.6"><span style="display:inline-block;width:11px;height:11px;' +
      'border-radius:50%;background:' + (typeColor[k] || '#0f766e') + ';margin-right:6px"></span>' +
      (typeName[k] || k) + '</div>').join('');
    el.innerHTML = '<div style="background:#fff;border-radius:8px;padding:8px 10px;' +
      'box-shadow:0 1px 5px rgba(0,0,0,.3);font-size:13px;max-width:240px">' +
      '<b>' + T.legend + '</b>' + wrows +
      (rows ? '<br><b>' + T.rings + '</b>' + rows : '') + '</div>';
    return el;
  }
});
map.addControl(new Legend({ position: 'bottomleft' }));

// Zoom so everyone is on screen.
if (latLngs.length > 1) map.fitBounds(latLngs, { padding: [45, 45], maxZoom: 13 });
</script>
</body></html>"""
