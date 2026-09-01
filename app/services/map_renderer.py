"""
Render a pastoralist-friendly map of a water point's species rings
(cattle/shoat/camel) as a PNG for WhatsApp / HTTP.

Base map: OpenStreetMap raster tiles (public tile server, low-volume usage --
respect their tile usage policy by a real User-Agent and a short timeout).
Overlay: the three piosphere ring polygons from PostGIS, drawn outer-to-inner
so the narrowest ring stays visible, plus a satellite pasture-quality layer.

Pastoralist-first design decisions:
  * The map is CENTERED ON THE HERDER, with a blue "Wewe hapa / You are here"
    pin, a red water-source pin, and the distance + direction between them.
  * A big bold place banner (ward - county) always says WHERE you are.
  * Bold TrueType fonts (DejaVu/Arial) so labels survive WhatsApp's
    downscaling -- tiny default-PIL fonts are illegible on a phone.
  * Directions use Swahili words (Kaskazini, Kusini, Mashariki, Magharibi, ...)
    that pastoralists use, not abstract "NE" abbreviations.
  * The best-pasture arrow points at the NEAREST walkable good patch, not a
    far-away global centroid.
  * Other nearby water sources are drawn as small landmark dots with names, so
    the map is anchored by familiar places even at wide (camel) zoom.

Everything is pure PIL + stdlib math (Web Mercator is a closed-form transform,
no projection library needed). Fallback: if the tile server is unreachable the
map still renders with a plain background so WhatsApp users always get *a* map.
"""
from __future__ import annotations

import io
import json
import math
import os
import urllib.request
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import shape

from app.config import get_advisory_thresholds
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

# Full compass in the words herders actually use (Swahili first, English fallback).
COMPASS_SWA = [
    "Kaskazini", "Kaskazini-Mashariki", "Mashariki", "Kusini-Mashariki",
    "Kusini", "Kusini-Magharibi", "Magharibi", "Kaskazini-Magharibi",
]
COMPASS_ENG = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

# Text in the pastoralist's language.
_UI = {
    "swa": {
        "here": "Wewe hapa",
        "water": "hadi maji",
        "best": "Malisho bora",
        "pasture": "malisho bora",
    },
    "eng": {
        "here": "You are here",
        "water": "to water",
        "best": "Best pasture",
        "pasture": "best pasture",
    },
}

_FONT_PATHS = [
    # Windows
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    # Linux (Render / Debian): DejaVu ships with most base images.
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
_FONT_PATH: str | None = None
_FONT_CACHE: dict[tuple[int, bool], ImageFont.ImageFont] = {}


def _get_font(size: int, bold: bool = True) -> ImageFont.ImageFont:
    """A bold TrueType font at the requested size (tiny default PIL fonts are
    unreadable on phones); falls back to the bitmap font when no TTF exists."""
    global _FONT_PATH
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    if _FONT_PATH is None:
        for p in _FONT_PATHS:
            if os.path.exists(p):
                _FONT_PATH = p
                break
    try:
        if _FONT_PATH:
            font = ImageFont.truetype(_FONT_PATH, size)
        else:
            try:
                font = ImageFont.load_default(size=size)  # Pillow >= 10.1
            except TypeError:
                font = ImageFont.load_default()
    except Exception:  # noqa: BLE001
        try:
            font = ImageFont.load_default(size=size)
        except TypeError:
            font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def _mercator_x(lon):
    """Web Mercator x (m) from longitude. Vectorised (numpy-safe)."""
    return np.asarray(lon, dtype=float) * 20037508.34 / 180.0


def _mercator_y(lat):
    """Web Mercator y (m) from latitude. Vectorised (numpy-safe)."""
    r = np.asarray(lat, dtype=float)
    return 20037508.34 / 180.0 * (180.0 / np.pi * np.log(np.tan(np.pi / 4 + np.radians(r) / 2)))


def _lonlat_to_px(lon: float, lat: float, west: float, north: float, mpp: float) -> tuple[float, float]:
    """EPSG:3857 -> image pixel for the given viewport."""
    x = (_mercator_x(lon) - west) / mpp
    y = (north - _mercator_y(lat)) / mpp
    return x, y


@lru_cache(maxsize=48)
def _fetch_tile(z: int, x: int, y: int) -> Image.Image | None:
    url = f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in (1, 2):  # one retry -- transient tile-server blips are common
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
                     herder_lat: float | None = None, species: str | None = None,
                     pasture: bool = True, lang: str = "swa",
                     confirm_source_id: str | None = None,
                     numbered_sources: list[dict] | None = None) -> bytes:
    """Render the water point's rings -- and, when pasture=True, the actual
    satellite forage-quality layer -- to PNG bytes. Raises ValueError if the
    water source (or its zones) doesn't exist.

    When the herder's location is supplied, the view is centered on THE HERDER
    (not the water point) and both are marked, so the map answers "where am I
    relative to the water?" instead of showing a far-away circle. `lang` is
    "swa" (default) or "eng" for labels and direction words.

    `confirm_source_id` highlights the herder's CONFIRMED water point (a bigger
    pin labelled "your water"); `numbered_sources` is a list of
    {water_source_id, lon, lat, ward} dicts drawn as numbered 1..N markers so
    the map matches a numbered choice list sent to the herder.
    """
    ws = next((w for w in water_sources.list_water_sources() if w.id == water_source_id), None)
    if ws is None:
        raise ValueError(f"water_source {water_source_id} not found")
    zones = water_sources.zones_for_water_source(water_source_id)
    if not zones:
        raise ValueError(f"water_source {water_source_id} has no species rings")

    lat, lon = ws.lat, ws.lon
    c_lat = herder_lat if herder_lat is not None else lat
    c_lon = herder_lon if herder_lon is not None else lon

    # Zoom so the HERDER'S species ring fits (more local + accurate); default
    # to the widest ring when no species is known.
    if species and any(z["species"] == species for z in zones):
        max_radius_km = max(z["radius_km"] for z in zones if z["species"] == species)
    else:
        max_radius_km = max(z["radius_km"] for z in zones)

    zoom = _compute_zoom(c_lat, max_radius_km)
    mpp = 156543.03392 * math.cos(math.radians(c_lat)) / (2 ** zoom)

    center_x, center_y = _mercator_x(c_lon), _mercator_y(c_lat)
    half = IMG_SIZE / 2 * mpp
    west, north = center_x - half, center_y + half

    img = _build_base_map(zoom, center_x, center_y, mpp)

    # Pasture-quality overlay (satellite forage classification) under the rings.
    best_pasture: tuple[float, float, int] | None = None
    pasture_note: str | None = None
    pasture_available = False
    if pasture:
        pasture_img, best_pasture, pasture_note = _build_pasture_overlay(
            water_source_id, west, north, mpp, herder_lon, herder_lat
        )
        if pasture_img is not None:
            img = Image.alpha_composite(img.convert("RGBA"), pasture_img)
            pasture_available = True

    overlay = Image.new("RGBA", (IMG_SIZE, IMG_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Other nearby water sources as familiar landmark dots (under the rings).
    _draw_nearby_water(draw, west, north, mpp, exclude=water_source_id)

    # Draw rings outer -> inner so the smallest stays on top.
    for zone in sorted(zones, key=lambda z: -z["radius_km"]):
        geom = shape(json.loads(zone["geojson"]))
        geom = geom.simplify(tolerance=0.0004, preserve_topology=True)
        style = RING_STYLE[zone["species"]]
        if geom.geom_type == "Polygon":
            rings_ = [geom.exterior.coords]
        elif geom.geom_type == "MultiPolygon":
            rings_ = [p.exterior.coords for p in geom.geoms]
        else:
            continue
        for ring in rings_:
            pts = [_lonlat_to_px(px, py, west, north, mpp) for px, py in ring]
            draw.polygon(pts, fill=style[0], outline=style[1], width=3)

    # No satellite data yet: never show a blank map — draw a clear "data is
    # being prepared" notice + a light loading hatch so it's obvious why the
    # colours aren't there (and that it's temporary).
    if pasture and not pasture_available:
        _draw_no_cog_notice(draw, lang)

    wx, wy = _lonlat_to_px(lon, lat, west, north, mpp)
    _draw_pin(draw, wx, wy, fill=(220, 38, 38, 255), label=ws.name or ws.ward or "Maji")

    # Numbered markers (1..N) so a numbered choice list matches the map.
    if numbered_sources:
        _draw_numbered_sources(draw, west, north, mpp, numbered_sources)

    # Highlight the herder's CONFIRMED water point with a distinct label.
    if confirm_source_id:
        _draw_confirmed_source(draw, west, north, mpp, confirm_source_id, ws.id, lang)

    if herder_lon is not None and herder_lat is not None:
        hx, hy = _lonlat_to_px(herder_lon, herder_lat, west, north, mpp)
        draw.line((hx, hy, wx, wy), fill=(30, 64, 175, 220), width=4)
        _draw_pin(draw, hx, hy, fill=(37, 99, 235, 255), label=_UI[lang]["here"])
        dist_km = _haversine_km(herder_lat, herder_lon, lat, lon)
        dist_txt = f"{dist_km:.1f} km" if dist_km >= 1 else f"{dist_km * 1000:.0f} m"
        w_bearing = _bearing_deg(herder_lat, herder_lon, lat, lon)
        w_dir = _compass_swa(w_bearing)
        _draw_badge(
            draw,
            (hx + 16, hy - 16),
            f"{dist_txt} {_UI[lang]['water']}  [{w_dir}]",
            fill=(37, 99, 235, 235),
        )
        # Green arrow to the NEAREST usable pasture patch, with distance +
        # direction in the herder's words, in a big readable banner.
        if best_pasture is not None and (abs(best_pasture[0] - herder_lon) > 1e-6
                                         or abs(best_pasture[1] - herder_lat) > 1e-6):
            px_, py_ = _lonlat_to_px(best_pasture[0], best_pasture[1], west, north, mpp)
            _draw_direction_arrow(draw, hx, hy, px_, py_)
            bearing = _bearing_deg(herder_lat, herder_lon, best_pasture[1], best_pasture[0])
            direction = _compass_swa(bearing)
            bp_dist = _haversine_km(herder_lat, herder_lon, best_pasture[1], best_pasture[0])
            bp_txt = f"{bp_dist:.1f} km" if bp_dist >= 1 else f"{bp_dist * 1000:.0f} m"
            _draw_banner_bottom(
                draw,
                f"{_UI[lang]['best']}: {direction}  -  {bp_txt}",
                fill=(22, 101, 52, 240),
            )

    _draw_place_banner(draw, f"{ws.name or ws.ward or 'Maji'}  -  {ws.county or ''}".strip())
    _draw_legend(draw, zones, ward=ws.name or ws.ward, county=ws.county,
                 pasture_note=pasture_note, lang=lang)
    _draw_scale_bar(draw, mpp)
    _draw_compass(draw, lang=lang)

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


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r1, r2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(r2)
    x = math.cos(r1) * math.sin(r2) - math.sin(r1) * math.cos(r2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _compass_swa(bearing: float) -> str:
    """8-point compass label in Swahili (0 = Kaskazini / north)."""
    idx = int((bearing + 22.5) // 45) % 8
    return COMPASS_SWA[idx]


def _compass_label(bearing: float) -> str:
    """8-point compass label in English (N/NE/E/...)."""
    idx = int((bearing + 22.5) // 45) % 8
    return COMPASS_ENG[idx]


def compass_swa(bearing: float) -> str:
    """Public Swahili compass label (used by the WhatsApp flow for captions)."""
    return _compass_swa(bearing)


def water_guidance(lat: float, lon: float, ws_lat: float, ws_lon: float) -> tuple[float, float]:
    """(bearing_deg, dist_km) from the herder to a water source."""
    return _bearing_deg(lat, lon, ws_lat, ws_lon), _haversine_km(lat, lon, ws_lat, ws_lon)


def _draw_pin(draw: ImageDraw.ImageDraw, x: float, y: float, fill: tuple, label: str) -> None:
    """A map pin (circle with a point) + a big readable label below it."""
    r = 11
    draw.ellipse((x - r, y - r, x + r, y + r), fill=fill, outline=(255, 255, 255, 255), width=4)
    draw.polygon([(x - 4, y + r - 2), (x + 4, y + r - 2), (x, y + r + 11)], fill=fill)
    font = _get_font(20)
    tw = draw.textlength(label, font=font)
    bx0, by0 = x - tw / 2 - 8, y + r + 14
    draw.rounded_rectangle((bx0, by0, bx0 + tw + 16, by0 + 32), radius=6,
                           fill=(255, 255, 255, 235), outline=(40, 40, 40, 180), width=1)
    draw.text((x - tw / 2, by0 + 5), label, fill=(20, 20, 20), font=font)


def _draw_badge(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str,
                fill: tuple = (37, 99, 235, 230)) -> None:
    font = _get_font(22)
    tw = draw.textlength(text, font=font)
    x, y = xy
    draw.rounded_rectangle((x, y, x + tw + 16, y + 34), radius=7, fill=fill,
                           outline=(255, 255, 255, 220), width=2)
    draw.text((x + 8, y + 5), text, fill=(255, 255, 255), font=font)


def _draw_place_banner(draw: ImageDraw.ImageDraw, text: str) -> None:
    """Big top-centre banner: the ward/county so the map always says WHERE."""
    font = _get_font(30)
    tw = draw.textlength(text, font=font)
    x0 = max(10, (IMG_SIZE - tw) / 2 - 22)
    y0 = 14
    w = min(tw + 44, IMG_SIZE - 20)
    h = 56
    draw.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=14,
                           fill=(255, 255, 255, 240), outline=(20, 20, 20, 200), width=2)
    draw.text((x0 + 22, y0 + 9), text, fill=(20, 20, 20), font=font)


def _draw_banner_bottom(draw: ImageDraw.ImageDraw, text: str, fill: tuple = (22, 101, 52, 240)) -> None:
    """Big bottom-centre banner: direction + distance to the best pasture."""
    font = _get_font(28)
    tw = draw.textlength(text, font=font)
    x0 = max(10, (IMG_SIZE - tw) / 2 - 20)
    y0 = IMG_SIZE - 96
    w = min(tw + 40, IMG_SIZE - 20)
    h = 54
    draw.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=14, fill=fill,
                           outline=(255, 255, 255, 230), width=3)
    draw.text((x0 + 20, y0 + 9), text, fill=(255, 255, 255), font=font)


def _draw_scale_bar(draw: ImageDraw.ImageDraw, mpp: float) -> None:
    """Scale bar (10/5/2/1 km depending on zoom) bottom-left, big text."""
    for km in (10, 5, 2, 1):
        px = km * 1000 / mpp
        if px <= 300:
            break
    font = _get_font(20)
    x0, y0 = 20, IMG_SIZE - 40
    draw.rounded_rectangle((x0 - 8, y0 - 8, x0 + px + 8, y0 + 32), radius=4,
                           fill=(255, 255, 255, 215))
    draw.rectangle((x0, y0, x0 + px, y0 + 5), fill=(40, 40, 40))
    draw.text((x0, y0 + 9), f"{km} km", fill=(40, 40, 40), font=font)


def _draw_compass(draw: ImageDraw.ImageDraw, lang: str = "swa") -> None:
    """A clear north arrow (top-right under the legend), big enough to read."""
    cx, cy = IMG_SIZE - 58, 150
    draw.rounded_rectangle((cx - 30, cy - 30, cx + 30, cy + 30), radius=8,
                           fill=(255, 255, 255, 225), outline=(40, 40, 40, 200), width=2)
    draw.line((cx, cy - 20, cx, cy + 20), fill=(40, 40, 40), width=3)
    draw.polygon([(cx, cy - 24), (cx - 7, cy - 12), (cx + 7, cy - 12)], fill=(220, 38, 38))
    font = _get_font(20)
    draw.text((cx - 9, cy + 22), "N", fill=(40, 40, 40), font=font)


def _build_base_map(zoom: int, center_x: float, center_y: float, mpp: float) -> Image.Image:
    """Assemble the OSM tile mosaic for the viewport; blank fallback on failure.

    center_x/center_y are the viewport center in EPSG:3857 metres. Pixel (col,
    row) in the world tile grid: col = world_px/2 + x/mpp_world,
    row = world_px/2 - y/mpp_world  (row grows SOUTHWARD; mercator y is
    positive north). The viewport is IMG_SIZE pixels around that center.

    Tiles are fetched concurrently with a short timeout so the map always
    renders in a few seconds even when the tile server is slow/unreachable
    (a blank beige background still gets drawn -- WhatsApp users always get a map).
    """
    import concurrent.futures

    img = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (228, 224, 216))
    world_px = TILE * (2 ** zoom)
    mpp_world = 40075016.686 / world_px
    # Centre pixel of the viewport in world-tile-pixel space.
    cx_px = world_px / 2 + center_x / mpp_world
    cy_px = world_px / 2 - center_y / mpp_world
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


def _draw_nearby_water(draw: ImageDraw.ImageDraw, west: float, north: float,
                       mpp: float, exclude: str) -> None:
    """Draw other nearby water sources as teal landmark dots with names, so the
    map is anchored by familiar places even at wide (camel) zoom."""
    try:
        sources = water_sources.list_water_sources()
    except Exception:  # noqa: BLE001
        return
    font = _get_font(16)
    drawn = 0
    for ws in sources:
        if ws.id == exclude:
            continue
        px_, py_ = _lonlat_to_px(ws.lon, ws.lat, west, north, mpp)
        if 0 <= px_ < IMG_SIZE and 0 <= py_ < IMG_SIZE:
            draw.ellipse((px_ - 7, py_ - 7, px_ + 7, py_ + 7),
                         fill=(14, 116, 144, 255), outline=(255, 255, 255, 255), width=2)
            label = ws.name or ws.ward or "Maji"
            tw = draw.textlength(label, font=font)
            bx0 = px_ - tw / 2 - 5
            by0 = py_ + 10
            draw.rounded_rectangle((bx0, by0, bx0 + tw + 10, by0 + 24), radius=4,
                                   fill=(255, 255, 255, 215))
            draw.text((px_ - tw / 2, by0 + 3), label, fill=(14, 116, 144), font=font)
            drawn += 1
            if drawn >= 6:
                break


def _draw_no_cog_notice(draw: ImageDraw.ImageDraw, lang: str = "swa") -> None:
    """A map must never be blank: draw a light 'loading' hatch + a clear
    banner explaining the satellite pasture layer is being prepared."""
    # Subtle diagonal hatch over the whole viewport.
    color = (245, 158, 11, 40)
    for x in range(-IMG_SIZE, IMG_SIZE * 2, 48):
        draw.line((x, 0, x + IMG_SIZE, IMG_SIZE), fill=color, width=2)
    text = {
        "swa": "Data ya malisho inaandaliwa kwa chanzo hiki - tazama tena baadaye",
        "eng": "Pasture data is being prepared for this water point - check back soon",
    }[lang]
    font = _get_font(24)
    tw = draw.textlength(text, font=font)
    x0 = max(10, (IMG_SIZE - tw) / 2 - 20)
    y0 = IMG_SIZE - 96
    w = min(tw + 40, IMG_SIZE - 20)
    h = 54
    draw.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=14,
                           fill=(245, 158, 11, 240), outline=(255, 255, 255, 230), width=3)
    draw.text((x0 + 20, y0 + 9), text, fill=(20, 20, 20), font=font)


def _draw_numbered_sources(draw: ImageDraw.ImageDraw, west: float, north: float,
                           mpp: float, sources: list[dict]) -> None:
    """Draw water-point markers numbered 1..N (matching the numbered choice
    list sent to the herder) so the map 'tells instantly' which is which."""
    font_big = _get_font(22)
    font_small = _get_font(16)
    for i, s in enumerate(sources, start=1):
        px_, py_ = _lonlat_to_px(s["lon"], s["lat"], west, north, mpp)
        if not (0 <= px_ < IMG_SIZE and 0 <= py_ < IMG_SIZE):
            continue
        r = 13
        draw.ellipse((px_ - r, py_ - r, px_ + r, py_ + r), fill=(37, 99, 235, 255),
                     outline=(255, 255, 255, 255), width=3)
        num = str(i)
        tw = draw.textlength(num, font=font_big)
        draw.text((px_ - tw / 2, py_ - 13), num, fill=(255, 255, 255), font=font_big)
        label = s.get("name") or s.get("ward") or "Maji"
        lw = draw.textlength(label, font=font_small)
        bx0 = px_ - lw / 2 - 5
        by0 = py_ + r + 4
        draw.rounded_rectangle((bx0, by0, bx0 + lw + 10, by0 + 24), radius=4,
                               fill=(255, 255, 255, 230))
        draw.text((px_ - lw / 2, by0 + 3), label, fill=(20, 20, 20), font=font_small)


def _draw_confirmed_source(draw: ImageDraw.ImageDraw, west: float, north: float,
                           mpp: float, confirm_source_id: str, main_source_id: str,
                           lang: str = "swa") -> None:
    """Highlight the herder's confirmed water point with a distinct 'your
    water' star-pin + label (only when it is not the main red pin)."""
    if confirm_source_id == main_source_id:
        return  # the main red pin already marks it
    try:
        ws = next((w for w in water_sources.list_water_sources() if w.id == confirm_source_id), None)
    except Exception:  # noqa: BLE001
        return
    if ws is None:
        return
    px_, py_ = _lonlat_to_px(ws.lon, ws.lat, west, north, mpp)
    if not (0 <= px_ < IMG_SIZE and 0 <= py_ < IMG_SIZE):
        return
    # A star-pin in teal, distinct from the red water pin.
    r = 12
    draw.ellipse((px_ - r, py_ - r, px_ + r, py_ + r), fill=(14, 116, 144, 255),
                 outline=(255, 255, 255, 255), width=3)
    draw.polygon([(px_ - 5, py_ + r - 3), (px_ + 5, py_ + r - 3), (px_, py_ + r + 10)],
                 fill=(14, 116, 144, 255))
    label = {"swa": "Maji yako", "eng": "Your water"}[lang]
    font = _get_font(20)
    tw = draw.textlength(label, font=font)
    bx0 = px_ - tw / 2 - 8
    by0 = py_ + r + 14
    draw.rounded_rectangle((bx0, by0, bx0 + tw + 16, by0 + 32), radius=6,
                           fill=(255, 255, 255, 235), outline=(40, 40, 40, 180), width=1)
    draw.text((px_ - tw / 2, by0 + 5), label, fill=(14, 116, 144), font=font)


def _draw_legend(draw: ImageDraw.ImageDraw, zones: list[dict], ward: str | None = None,
                 county: str | None = None, pasture_note: str | None = None,
                 lang: str = "swa") -> None:
    font = _get_font(17)
    line_h = 26
    x0, y0 = 16, 76
    items = [(zone["species"], RING_STYLE[zone["species"]][1])
             for zone in sorted(zones, key=lambda z: z["radius_km"])]

    pasture_rows = []
    if pasture_note:
        pasture_rows = [
            ("Pasture layer", None),
            ("  green = grass", (21, 128, 61, 255)),
            ("  olive = dry forage", (132, 204, 22, 255)),
            ("  red = bare", (220, 38, 38, 255)),
            ("  yellow = unclear", (245, 158, 11, 255)),
            (f"  {pasture_note}", None),
        ]

    header = f"{ward or 'Water source'} - {county}" if county else (ward or "Water source")
    header_h = 22
    box_w = 236
    box_h = header_h + len(items) * line_h + len(pasture_rows) * line_h + 14
    draw.rounded_rectangle((x0, y0, x0 + box_w, y0 + box_h), radius=6, fill=(255, 255, 255, 225))
    draw.text((x0 + 12, y0 + 5), header, fill=(20, 20, 20), font=font)

    cy = y0 + header_h + 10
    for species, color in items:
        draw.ellipse((x0 + 12, cy - 5, x0 + 24, cy + 7), fill=color)
        draw.text((x0 + 32, cy - 9), RING_STYLE[species][2], fill=(40, 40, 40), font=font)
        cy += line_h

    for label, color in pasture_rows:
        if color:
            draw.rectangle((x0 + 12, cy - 7, x0 + 24, cy + 5), fill=color)
        draw.text((x0 + 32, cy - 9), label, fill=(40, 40, 40), font=font)
        cy += line_h


def _draw_direction_arrow(draw: ImageDraw.ImageDraw, x1: float, y1: float, x2: float, y2: float) -> None:
    """A thick green arrow from (x1,y1) to (x2,y2) with a big arrowhead."""
    draw.line((x1, y1, x2, y2), fill=(22, 101, 52, 255), width=7)
    ang = math.atan2(y2 - y1, x2 - x1)
    L = 24
    head1 = (x2 - L * math.cos(ang - 0.42), y2 - L * math.sin(ang - 0.42))
    head2 = (x2 - L * math.cos(ang + 0.42), y2 - L * math.sin(ang + 0.42))
    draw.polygon([(x2, y2), head1, head2], fill=(22, 101, 52, 255))
    # white outline so the arrow reads over any base-map colour
    draw.line((x1, y1, x2, y2), fill=(255, 255, 255, 200), width=1)


# --- pasture overlay ---------------------------------------------------------


def _nearest_good_patch(arr, transform, herder_lon=None, herder_lat=None):
    """Classify the overview stack and return the best usable pasture patch.

    Returns ((lon, lat, score), classes). The patch is the NEAREST good cluster
    to the herder (good pixels within ~2 km of the closest good pixel), so the
    green arrow gives local, walkable guidance instead of pointing at a
    far-away global centroid. Falls back to the global weighted centroid when
    no herder position is supplied. score = 3 (grass) or 2 (dry forage).
    """
    t = get_advisory_thresholds().vegetation
    ndvi, satvi, bsi = arr[0], arr[1], arr[2]

    good = np.isfinite(ndvi) & np.isfinite(satvi) & np.isfinite(bsi)
    classes = np.full(ndvi.shape, 0, dtype=np.uint8)  # 0 = nodata
    # 1 green | 2 dry forage | 3 bare | 4 uncertain
    classes[good & (ndvi >= t["ndvi_green_threshold"])] = 1
    classes[good & (ndvi < t["ndvi_green_threshold"])
            & (satvi >= t["satvi_dry_forage_threshold"]) & (bsi <= t["bsi_low_threshold"])] = 2
    classes[good & (satvi < t["satvi_bare_threshold"])] = 3
    classes[good & (bsi >= t["bsi_high_threshold"])] = 3
    classes[good & (classes == 0)] = 4  # uncertain

    score = np.where(classes == 1, 3.0, np.where(classes == 2, 2.0, 0.0))
    good_px = score > 0
    if not good_px.any():
        return None, classes

    c0, f0 = transform.c, transform.f
    a_, e_ = transform.a, transform.e
    rows_i, cols_i = np.nonzero(good_px)
    wgt_all = score[good_px]
    if herder_lon is not None and herder_lat is not None:
        h_col = (herder_lon - c0) / a_
        h_row = (f0 - herder_lat) / e_
        d0 = np.hypot((cols_i - h_col) * a_, (rows_i - h_row) * abs(e_))
        k = int(np.argmin(d0))
        d2 = np.hypot((cols_i - cols_i[k]) * a_, (rows_i - rows_i[k]) * abs(e_))
        mask = d2 <= 2000.0  # walkable ~2 km cluster
        cols_c, rows_c = cols_i[mask], rows_i[mask]
        wgt = wgt_all[mask]
    else:
        cols_c, rows_c = cols_i, rows_i
        wgt = wgt_all

    lons = c0 + cols_c * a_
    lats = f0 + rows_c * e_
    mx = _mercator_x(lons.astype(float))
    my = _mercator_y(lats.astype(float))
    mx_c = float(np.average(mx, weights=wgt))
    my_c = float(np.average(my, weights=wgt))
    best = (float(_mercator_x_inv(mx_c)), float(_mercator_y_inv(my_c)), int(score.max()))
    return best, classes


def _build_pasture_overlay(water_source_id, west, north, mpp, herder_lon=None, herder_lat=None):
    """Classify each COG pixel into a forage class and build an RGBA overlay.

    Returns (overlay_image_or_None, best_pasture_(lon,lat,score), note).
    """
    from app.services.raster_read import read_overview_array

    res = read_overview_array(water_source_id, bands=[1, 3, 4], max_dim=512)
    if res is None:
        return None, None, None
    arr, transform = res
    if arr.shape[0] < 3:
        return None, None, None

    import numpy as np

    best, classes = _nearest_good_patch(arr, transform, herder_lon, herder_lat)

    # Fraction of valid pixels that are usable forage (for the legend note).
    usable = int((classes == 1).sum()) + int((classes == 2).sum())
    valid_px = int((classes > 0).sum())
    note = None
    if valid_px:
        note = f"{100 * usable / valid_px:.0f}% usable pasture"

    # Sample the classification into the IMG_SIZE viewport with an accurate
    # per-pixel geolocation (inverse Mercator), then build the RGBA overlay.
    # Kept memory-slim: only a pair of 1-D coordinate vectors + two clipped
    # int32 index arrays (no full-size float64 lattice).
    color_map = {
        0: (0, 0, 0, 0),
        1: (21, 128, 61, 110),
        2: (132, 204, 22, 105),
        3: (220, 38, 38, 105),
        4: (245, 158, 11, 75),
    }
    h, w = classes.shape
    c0, f0 = transform.c, transform.f
    a_, e_ = transform.a, transform.e
    lons = _mercator_x_inv(west + np.arange(IMG_SIZE, dtype=np.float32) * mpp)
    lats = _mercator_y_inv(north - np.arange(IMG_SIZE, dtype=np.float32) * mpp)
    col_i = np.clip(np.rint((lons[None, :] - c0) / a_).astype(np.int32), 0, w - 1)
    row_i = np.clip(np.rint((f0 - lats[:, None]) / e_).astype(np.int32), 0, h - 1)
    in_bounds = (((lons[None, :] - c0) / a_ >= 0) & ((lons[None, :] - c0) / a_ <= w - 1)
                 & ((f0 - lats[:, None]) / e_ >= 0) & ((f0 - lats[:, None]) / e_ <= h - 1))
    sampled = classes[row_i, col_i]
    sampled[~in_bounds] = 0

    out_rgba = np.zeros((IMG_SIZE, IMG_SIZE, 4), dtype=np.uint8)
    for k, color in color_map.items():
        out_rgba[sampled == k] = color
    return Image.fromarray(out_rgba, "RGBA"), best, note


def pasture_guidance(water_source_id: str, herder_lon: float, herder_lat: float):
    """(bearing_deg, dist_km) from the herder to the nearest usable pasture
    patch, or None when the COG is unavailable. Lightweight: only reads the
    small overview, no rendering. Used by the WhatsApp flow for captions."""
    from app.services.raster_read import read_overview_array

    try:
        res = read_overview_array(water_source_id, bands=[1, 3, 4], max_dim=512)
    except Exception:  # noqa: BLE001
        return None
    if res is None:
        return None
    arr, transform = res
    if arr.shape[0] < 3:
        return None
    best, _ = _nearest_good_patch(arr, transform, herder_lon, herder_lat)
    if best is None:
        return None
    bearing = _bearing_deg(herder_lat, herder_lon, best[1], best[0])
    dist_km = _haversine_km(herder_lat, herder_lon, best[1], best[0])
    return bearing, dist_km


def _mercator_x_inv(x):
    """Inverse Web Mercator x (m) -> longitude. Vectorised (numpy-safe)."""
    return np.asarray(x, dtype=float) * 180.0 / 20037508.34


def _mercator_y_inv(y):
    """Inverse Web Mercator y (m) -> latitude. Vectorised (numpy-safe)."""
    return np.degrees(np.arctan(np.sinh(np.asarray(y, dtype=float) * np.pi / 20037508.34)))
