"""
Render a herder-friendly map of a water point's species rings (cattle/shoat/camel)
as a PNG for WhatsApp / HTTP.

Base map: OpenStreetMap raster tiles (public tile server, low-volume usage —
respect their tile usage policy by a real User-Agent and a short timeout).
Overlay: the three piosphere ring polygons from PostGIS, drawn outer-to-inner
so the narrowest ring stays visible, plus a legend and the water point marker.

Everything is pure PIL + stdlib math (Web Mercator is a closed-form transform,
no projection library needed). Fallback: if the tile server is unreachable the
map still renders with a plain background so WhatsApp users always get *a* map.
"""
from __future__ import annotations

import io
import json
import math
import urllib.request
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import shape

from app.services import water_sources

IMG_SIZE = 1024
TILE = 256
USER_AGENT = "ArdaLink-Piosphere-Advisory/0.1 (pastoral advisory WhatsApp bot)"

# Species ring style: (fill RGBA, outline RGBA, label)
RING_STYLE = {
    "cattle": ((59, 130, 246, 60), (37, 99, 235, 255), "cattle (7km)"),
    "shoat": ((16, 185, 129, 60), (5, 150, 105, 255), "shoat (11km)"),
    "camel": ((249, 115, 22, 55), (234, 88, 12, 255), "camel (25km)"),
}


def _mercator_x(lon: float) -> float:
    return lon * 20037508.34 / 180.0


def _mercator_y(lat: float) -> float:
    return 20037508.34 / 180.0 * (180.0 / math.pi * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def _lonlat_to_px(lon: float, lat: float, west: float, north: float, mpp: float) -> tuple[float, float]:
    """EPSG:3857 -> image pixel for the given viewport."""
    x = (_mercator_x(lon) - west) / mpp
    y = (north - _mercator_y(lat)) / mpp
    return x, y


@lru_cache(maxsize=256)
def _fetch_tile(z: int, x: int, y: int) -> Image.Image | None:
    url = f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:  # noqa: BLE001  (tile server unreachable — caller falls back)
        return None


def _compute_zoom(lat: float, max_radius_km: float) -> int:
    """Pick the zoom so the full outer ring fits in the image with ~15% margin."""
    extent_m = max_radius_km * 1000 * 2 * 1.15
    mpp_target = extent_m / IMG_SIZE
    z = math.log2(156543.03392 * math.cos(math.radians(lat)) / mpp_target)
    return max(8, min(14, int(round(z))))


def render_rings_png(water_source_id: str) -> bytes:
    """Render the water point's species rings to PNG bytes. Raises ValueError if
    the water source (or its zones) doesn't exist."""
    ws = next((w for w in water_sources.list_water_sources() if w.id == water_source_id), None)
    if ws is None:
        raise ValueError(f"water_source {water_source_id} not found")
    zones = water_sources.zones_for_water_source(water_source_id)
    if not zones:
        raise ValueError(f"water_source {water_source_id} has no species rings")

    lat, lon = ws.lat, ws.lon
    max_radius_km = max(z["radius_km"] for z in zones)
    zoom = _compute_zoom(lat, max_radius_km)
    mpp = 156543.03392 * math.cos(math.radians(lat)) / (2 ** zoom)  # meters per pixel

    # Viewport in EPSG:3857 centered on the water point.
    center_x, center_y = _mercator_x(lon), _mercator_y(lat)
    half = IMG_SIZE / 2 * mpp
    west, north = center_x - half, center_y + half

    img = _build_base_map(zoom, west, north, mpp)
    draw = ImageDraw.Draw(img)

    # Draw rings outer -> inner so the smallest stays on top.
    for zone in sorted(zones, key=lambda z: -z["radius_km"]):
        geom = shape(json.loads(zone["geojson"]))
        geom = geom.simplify(tolerance=0.0004, preserve_topology=True)  # ~45m, plenty for a map


def _build_base_map(zoom: int, west: float, north: float, mpp: float) -> Image.Image:
    """Assemble the OSM tile mosaic for the viewport; blank fallback on failure."""
    img = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (228, 224, 216))
    # World in mercator at this zoom: 40075016.686 m over 256*2^z px.
    world_px = TILE * (2 ** zoom)
    mpp_world = 40075016.686 / world_px
    # Center pixel of the viewport in world-tile-pixel space.
    cx_px = west / mpp_world + world_px / 2
    cy_px = north / mpp_world + world_px / 2  # mercator y grows downward
    tiles_per_side = IMG_SIZE // TILE + 1
    half = IMG_SIZE / 2 / mpp_world
    left_px, top_px = cx_px - half, cy_px - half
    tile_x0 = int(math.floor(left_px / TILE))
    tile_y0 = int(math.floor(top_px / TILE))
    n = 2 ** zoom
    for ty in range(tile_y0, tile_y0 + tiles_per_side + 1):
        for tx in range(tile_x0, tile_x0 + tiles_per_side + 1):
            tile_img = _fetch_tile(zoom, tx % n, ty % n)
            if tile_img is None:
                continue
            px = int(tx * TILE - left_px)
            py = int(ty * TILE - top_px)
            if -TILE < px < IMG_SIZE and -TILE < py < IMG_SIZE:
                img.paste(tile_img, (px, py))
    return img


def _draw_marker(draw: ImageDraw.ImageDraw, west: float, north: float, mpp: float, lon: float, lat: float) -> None:
    mx, my = _lonlat_to_px(lon, lat, west, north, mpp)
    r = 9
    draw.ellipse((mx - r, my - r, mx + r, my + r), fill=(220, 38, 38, 255), outline=(255, 255, 255, 255), width=3)


def _draw_legend(draw: ImageDraw.ImageDraw, zones: list[dict]) -> None:
    font = ImageFont.load_default()
    line_h = 26
    x0, y0 = 16, 16
    items = [(zone["species"], RING_STYLE[zone["species"]][1])
             for zone in sorted(zones, key=lambda z: z["radius_km"])]
    box_w = 190
    box_h = 18 + len(items) * line_h
    draw.rounded_rectangle((x0, y0, x0 + box_w, y0 + box_h), radius=6, fill=(255, 255, 255, 210))
    for i, (species, color) in enumerate(items):
        cy = y0 + 14 + i * line_h
        draw.ellipse((x0 + 12, cy - 5, x0 + 24, cy + 7), fill=color)
        label = RING_STYLE[species][2]
        draw.text((x0 + 32, cy - 8), label, fill=(40, 40, 40), font=font)

        style = RING_STYLE[zone["species"]]
        if geom.geom_type == "Polygon":
            rings = [geom.exterior.coords]
        elif geom.geom_type == "MultiPolygon":
            rings = [p.exterior.coords for p in geom.geoms]
        else:
            continue
        for ring in rings:
            pts = [_lonlat_to_px(px, py, west, north, mpp) for px, py in ring]
            draw.polygon(pts, fill=style[0], outline=style[1], width=3)

    _draw_legend(draw, zones)
    _draw_marker(draw, west, north, mpp, lon, lat)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
