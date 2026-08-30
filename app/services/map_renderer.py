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
    for attempt in (1, 2):  # one retry — transient tile-server blips are common
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = resp.read()
            return Image.open(io.BytesIO(data)).convert("RGB")
        except Exception:  # noqa: BLE001
            continue
    return None


def _compute_zoom(lat: float, max_radius_km: float) -> int:
    """Pick the zoom so the full outer ring fits in the image with ~15% margin."""
    extent_m = max_radius_km * 1000 * 2 * 1.15
    mpp_target = extent_m / IMG_SIZE
    z = math.log2(156543.03392 * math.cos(math.radians(lat)) / mpp_target)
    return max(8, min(14, int(round(z))))


def render_rings_png(water_source_id: str, herder_lon: float | None = None,
                     herder_lat: float | None = None) -> bytes:
    """Render the water point's species rings to PNG bytes. Raises ValueError if
    the water source (or its zones) doesn't exist.

    When the herder's location is supplied, the view is centered on THE HERDER
    (not the water point) and both markers are drawn: blue "You are here" and a
    red water-source pin with a distance label — so the map answers "where am I
    relative to the water?" instead of showing a far-away circle.
    """
    ws = next((w for w in water_sources.list_water_sources() if w.id == water_source_id), None)
    if ws is None:
        raise ValueError(f"water_source {water_source_id} not found")
    zones = water_sources.zones_for_water_source(water_source_id)
    if not zones:
        raise ValueError(f"water_source {water_source_id} has no species rings")

    lat, lon = ws.lat, ws.lon
    # Center the viewport on the herder when we know their position.
    c_lat = herder_lat if herder_lat is not None else lat
    c_lon = herder_lon if herder_lon is not None else lon

    max_radius_km = max(z["radius_km"] for z in zones)
    zoom = _compute_zoom(c_lat, max_radius_km)
    mpp = 156543.03392 * math.cos(math.radians(c_lat)) / (2 ** zoom)  # meters per pixel

    center_x, center_y = _mercator_x(c_lon), _mercator_y(c_lat)
    half = IMG_SIZE / 2 * mpp
    west, north = center_x - half, center_y + half

    img = _build_base_map(zoom, west, north, mpp)
    overlay = Image.new("RGBA", (IMG_SIZE, IMG_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Draw rings outer -> inner so the smallest stays on top.
    for zone in sorted(zones, key=lambda z: -z["radius_km"]):
        geom = shape(json.loads(zone["geojson"]))
        geom = geom.simplify(tolerance=0.0004, preserve_topology=True)  # ~45m, plenty for a map
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

    # Water source pin + its ward/county as a landmark label.
    wx, wy = _lonlat_to_px(lon, lat, west, north, mpp)
    _draw_pin(draw, wx, wy, fill=(220, 38, 38, 255), label=ws.ward or "Water")

    # "You are here" marker + straight-line distance to the water.
    if herder_lon is not None and herder_lat is not None:
        hx, hy = _lonlat_to_px(herder_lon, herder_lat, west, north, mpp)
        draw.line((hx, hy, wx, wy), fill=(30, 64, 175, 220), width=3)
        _draw_pin(draw, hx, hy, fill=(37, 99, 235, 255), label="You are here")
        dist_km = _haversine_km(herder_lat, herder_lon, lat, lon)
        _draw_badge(
            draw,
            (hx + 14, hy - 14),
            f"{dist_km:.1f} km to water" if dist_km >= 1 else f"{dist_km*1000:.0f} m to water",
        )

    _draw_legend(draw, zones, ward=ws.ward, county=ws.county)
    _draw_scale_bar(draw, mpp)
    _draw_compass(draw)

    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _draw_pin(draw: ImageDraw.ImageDraw, x: float, y: float, fill: tuple, label: str) -> None:
    """A map pin (circle with a point) + a small label below it."""
    r = 10
    draw.ellipse((x - r, y - r, x + r, y + r), fill=fill, outline=(255, 255, 255, 255), width=3)
    draw.polygon([(x - 4, y + r - 2), (x + 4, y + r - 2), (x, y + r + 9)], fill=fill)
    font = ImageFont.load_default()
    tw = draw.textlength(label, font=font)
    bx0, by0 = x - tw / 2 - 6, y + r + 12
    draw.rounded_rectangle((bx0, by0, bx0 + tw + 12, by0 + 16), radius=4,
                           fill=(255, 255, 255, 220))
    draw.text((x - tw / 2, by0 + 3), label, fill=(20, 20, 20), font=font)


def _draw_badge(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str) -> None:
    font = ImageFont.load_default()
    tw = draw.textlength(text, font=font)
    x, y = xy
    draw.rounded_rectangle((x, y, x + tw + 14, y + 18), radius=5, fill=(37, 99, 235, 230))
    draw.text((x + 7, y + 3), text, fill=(255, 255, 255), font=font)


def _draw_scale_bar(draw: ImageDraw.ImageDraw, mpp: float) -> None:
    """Draw a scale bar (2km / 5km depending on zoom) bottom-left."""
    font = ImageFont.load_default()
    for km in (10, 5, 2, 1):
        px = km * 1000 / mpp
        if px <= 300:
            break
    x0, y0 = 20, IMG_SIZE - 30
    draw.rounded_rectangle((x0 - 6, y0 - 6, x0 + px + 6, y0 + 12), radius=4,
                           fill=(255, 255, 255, 210))
    draw.rectangle((x0, y0, x0 + px, y0 + 5), fill=(40, 40, 40))
    draw.text((x0, y0 + 8), f"{km} km", fill=(40, 40, 40), font=font)


def _draw_compass(draw: ImageDraw.ImageDraw) -> None:
    """Small north arrow, top-right under the legend."""
    cx, cy = IMG_SIZE - 40, 120
    draw.rounded_rectangle((cx - 22, cy - 22, cx + 22, cy + 22), radius=6,
                           fill=(255, 255, 255, 200))
    draw.line((cx, cy - 14, cx, cy + 14), fill=(40, 40, 40), width=2)
    draw.polygon([(cx, cy - 17), (cx - 5, cy - 8), (cx + 5, cy - 8)], fill=(220, 38, 38))
    font = ImageFont.load_default()
    draw.text((cx - 3, cy + 16), "N", fill=(40, 40, 40), font=font)


def _build_base_map(zoom: int, west: float, north: float, mpp: float) -> Image.Image:
    """Assemble the OSM tile mosaic for the viewport; blank fallback on failure.

    Tiles are fetched concurrently with a short timeout so the map always
    renders in a few seconds even when the tile server is slow/unreachable
    (a blank beige background still gets drawn — WhatsApp users always get a map).
    """
    import concurrent.futures

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

    coords = []
    for ty in range(tile_y0, tile_y0 + tiles_per_side + 1):
        for tx in range(tile_x0, tile_x0 + tiles_per_side + 1):
            coords.append((zoom, tx % n, ty % n))

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda c: _fetch_tile(*c), coords))

    for (z, tx, ty), tile_img in zip(coords, results):
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


def _draw_legend(draw: ImageDraw.ImageDraw, zones: list[dict], ward: str | None = None,
                 county: str | None = None) -> None:
    font = ImageFont.load_default()
    line_h = 26
    x0, y0 = 16, 16
    items = [(zone["species"], RING_STYLE[zone["species"]][1])
             for zone in sorted(zones, key=lambda z: z["radius_km"])]
    header = f"{ward or 'Water source'} · {county}" if county else (ward or "Water source")
    header_h = 22
    box_w = 200
    box_h = header_h + len(items) * line_h + 12
    draw.rounded_rectangle((x0, y0, x0 + box_w, y0 + box_h), radius=6, fill=(255, 255, 255, 215))
    draw.text((x0 + 12, y0 + 6), header, fill=(20, 20, 20), font=font)
    for i, (species, color) in enumerate(items):
        cy = y0 + header_h + 10 + i * line_h
        draw.ellipse((x0 + 12, cy - 5, x0 + 24, cy + 7), fill=color)
        label = RING_STYLE[species][2]
        draw.text((x0 + 32, cy - 8), label, fill=(40, 40, 40), font=font)

