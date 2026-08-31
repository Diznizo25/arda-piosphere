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
                     herder_lat: float | None = None, species: str | None = None,
                     pasture: bool = True) -> bytes:
    """Render the water point's rings — and, when pasture=True, the actual
    satellite forage-quality layer — to PNG bytes. Raises ValueError if the
    water source (or its zones) doesn't exist.

    When the herder's location is supplied, the view is centered on THE HERDER
    (not the water point) and both markers are drawn: blue "You are here" and a
    red water-source pin with a distance label — so the map answers "where am I
    relative to the water?" instead of showing a far-away circle.

    With pasture=True the COG's SATVI/NDVI/BSI stack is rendered as a coloured
    overlay: green = growing grass, olive = dry forage, red = bare ground,
    yellow = unclear. A green arrow then points from the herder to the nearest
    patch of good pasture, with distance + direction.
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
    if pasture:
        pasture_img, best_pasture, pasture_note = _build_pasture_overlay(
            water_source_id, west, north, mpp
        )
        if pasture_img is not None:
            img = Image.alpha_composite(img.convert("RGBA"), pasture_img)

    overlay = Image.new("RGBA", (IMG_SIZE, IMG_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

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

    wx, wy = _lonlat_to_px(lon, lat, west, north, mpp)
    _draw_pin(draw, wx, wy, fill=(220, 38, 38, 255), label=ws.ward or "Water")

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
        # Green arrow to the best pasture patch, with distance + direction.
        if best_pasture is not None and (abs(best_pasture[0] - herder_lon) > 1e-6
                                         or abs(best_pasture[1] - herder_lat) > 1e-6):
            px_, py_ = _lonlat_to_px(best_pasture[0], best_pasture[1], west, north, mpp)
            _draw_direction_arrow(draw, hx, hy, px_, py_)
            bearing = _bearing_deg(herder_lat, herder_lon, best_pasture[1], best_pasture[0])
            direction = _compass_label(bearing)
            bp_dist = _haversine_km(herder_lat, herder_lon, best_pasture[1], best_pasture[0])
            label = (f"Best pasture {direction}"
                     + (f", {bp_dist:.1f} km" if bp_dist >= 1 else f", {bp_dist*1000:.0f} m"))
            _draw_badge(draw, (hx - 240, hy + 30), label, fill=(22, 101, 52, 235))

    _draw_legend(draw, zones, ward=ws.ward, county=ws.county, pasture_note=pasture_note)
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


def _draw_badge(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str,
                fill: tuple = (37, 99, 235, 230)) -> None:
    font = ImageFont.load_default()
    tw = draw.textlength(text, font=font)
    x, y = xy
    draw.rounded_rectangle((x, y, x + tw + 14, y + 18), radius=5, fill=fill)
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


def _build_base_map(zoom: int, center_x: float, center_y: float, mpp: float) -> Image.Image:
    """Assemble the OSM tile mosaic for the viewport; blank fallback on failure.

    center_x/center_y are the viewport center in EPSG:3857 metres. Pixel (col,
    row) in the world tile grid: col = world_px/2 + x/mpp_world,
    row = world_px/2 - y/mpp_world  (row grows SOUTHWARD; mercator y is
    positive north). The viewport is IMG_SIZE pixels around that center.

    Tiles are fetched concurrently with a short timeout so the map always
    renders in a few seconds even when the tile server is slow/unreachable
    (a blank beige background still gets drawn — WhatsApp users always get a map).
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


def _draw_marker(draw: ImageDraw.ImageDraw, west: float, north: float, mpp: float, lon: float, lat: float) -> None:
    mx, my = _lonlat_to_px(lon, lat, west, north, mpp)
    r = 9
    draw.ellipse((mx - r, my - r, mx + r, my + r), fill=(220, 38, 38, 255), outline=(255, 255, 255, 255), width=3)


def _draw_legend(draw: ImageDraw.ImageDraw, zones: list[dict], ward: str | None = None,
                 county: str | None = None, pasture_note: str | None = None) -> None:
    font = ImageFont.load_default()
    line_h = 24
    x0, y0 = 16, 16
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

    header = f"{ward or 'Water source'} · {county}" if county else (ward or "Water source")
    header_h = 20
    box_w = 210
    box_h = header_h + len(items) * line_h + len(pasture_rows) * line_h + 12
    draw.rounded_rectangle((x0, y0, x0 + box_w, y0 + box_h), radius=6, fill=(255, 255, 255, 225))
    draw.text((x0 + 12, y0 + 5), header, fill=(20, 20, 20), font=font)

    cy = y0 + header_h + 8
    for species, color in items:
        draw.ellipse((x0 + 12, cy - 5, x0 + 24, cy + 7), fill=color)
        draw.text((x0 + 32, cy - 8), RING_STYLE[species][2], fill=(40, 40, 40), font=font)
        cy += line_h

    for label, color in pasture_rows:
        if color:
            draw.rectangle((x0 + 12, cy - 6, x0 + 24, cy + 6), fill=color)
        draw.text((x0 + 32, cy - 8), label, fill=(40, 40, 40), font=font)
        cy += line_h



# --- pasture overlay ---------------------------------------------------------


def _build_pasture_overlay(water_source_id, west, north, mpp):
    """Classify each COG pixel into a forage class and build an RGBA overlay.

    Returns (overlay_image_or_None, best_pasture_(lon,lat,score), note).
    """
    from app.services.raster_read import read_overview_array

    res = read_overview_array(water_source_id)
    if res is None:
        return None, None, None
    arr, transform = res
    if arr.shape[0] < 4:
        return None, None, None

    import numpy as np

    t = get_advisory_thresholds().vegetation
    ndvi, satvi, bsi = arr[0], arr[2], arr[3]

    good = np.isfinite(ndvi) & np.isfinite(satvi) & np.isfinite(bsi)
    classes = np.full(ndvi.shape, 0, dtype=np.uint8)  # 0 = nodata
    # 1 green | 2 dry forage | 3 bare | 4 uncertain
    classes[good & (ndvi >= t["ndvi_green_threshold"])] = 1
    classes[good & (ndvi < t["ndvi_green_threshold"])
            & (satvi >= t["satvi_dry_forage_threshold"]) & (bsi <= t["bsi_low_threshold"])] = 2
    classes[good & (satvi < t["satvi_bare_threshold"])] = 3
    classes[good & (bsi >= t["bsi_high_threshold"])] = 3
    classes[good & (classes == 0)] = 4  # uncertain

    # Fraction of valid pixels that are usable forage (for the legend note).
    usable = int((classes == 1).sum()) + int((classes == 2).sum())
    valid_px = int(good.sum())
    note = None
    if valid_px:
        note = f"{100 * usable / valid_px:.0f}% usable pasture"

    # Best-pasture patch: centroid of good pixels (green or dry), weighted by a
    # simple score, converted back to lon/lat.
    best = None
    score = np.where(classes == 1, 3.0, np.where(classes == 2, 2.0, 0.0))
    good_px = score > 0
    if good_px.any():
        rows_i, cols_i = np.nonzero(good_px)
        wgt = score[good_px]
        c0, f0 = transform.c, transform.f
        a_, e_ = transform.a, transform.e
        lons = c0 + cols_i * a_
        lats = f0 + rows_i * e_
        mx = _mercator_x(lons.astype(float))
        my = _mercator_y(lats.astype(float))
        mx_c = float(np.average(mx, weights=wgt))
        my_c = float(np.average(my, weights=wgt))
        best = (float(_mercator_x_inv(mx_c)), float(_mercator_y_inv(my_c)), int(score.max()))

    # Sample the classification into the IMG_SIZE viewport with an accurate
    # per-pixel geolocation (inverse Mercator), then build the RGBA overlay.
    color_map = {
        0: (0, 0, 0, 0),
        1: (21, 128, 61, 110),
        2: (132, 204, 22, 105),
        3: (220, 38, 38, 105),
        4: (245, 158, 11, 75),
    }
    h, w = classes.shape
    iy, ix = np.mgrid[0:IMG_SIZE, 0:IMG_SIZE]
    lon_img = _mercator_x_inv(west + ix * mpp)
    lat_img = _mercator_y_inv(north - iy * mpp)
    c0, f0 = transform.c, transform.f
    a_, e_ = transform.a, transform.e
    col = (lon_img - c0) / a_
    row = (f0 - lat_img) / e_
    col_i = np.clip(np.round(col).astype(int), 0, w - 1)
    row_i = np.clip(np.round(row).astype(int), 0, h - 1)
    in_bounds = (col >= 0) & (col <= w - 1) & (row >= 0) & (row <= h - 1)
    sampled = classes[row_i, col_i]
    sampled[~in_bounds] = 0

    out_rgba = np.zeros((IMG_SIZE, IMG_SIZE, 4), dtype=np.uint8)
    for k, color in color_map.items():
        out_rgba[sampled == k] = color
    return Image.fromarray(out_rgba, "RGBA"), best, note


def _mercator_x_inv(x):
    """Inverse Web Mercator x (m) -> longitude. Vectorised (numpy-safe)."""
    import numpy as np

    return np.asarray(x, dtype=float) * 180.0 / 20037508.34


def _mercator_y_inv(y):
    """Inverse Web Mercator y (m) -> latitude. Vectorised (numpy-safe)."""
    import numpy as np

    return np.degrees(np.arctan(np.sinh(np.asarray(y, dtype=float) * np.pi / 20037508.34)))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r1, r2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(r2)
    x = math.cos(r1) * math.sin(r2) - math.sin(r1) * math.cos(r2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _compass_label(bearing: float) -> str:
    """8-point compass label for a bearing (degrees, 0=N)."""
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int((bearing + 22.5) // 45) % 8
    return dirs[idx]


def _draw_direction_arrow(draw, x1: float, y1: float, x2: float, y2: float) -> None:
    """Draw a green arrow from (x1,y1) to (x2,y2) with a small head."""
    draw.line((x1, y1, x2, y2), fill=(22, 101, 52, 255), width=4)
    ang = math.atan2(y2 - y1, x2 - x1)
    L = 14
    head1 = (x2 - L * math.cos(ang - 0.45), y2 - L * math.sin(ang - 0.45))
    head2 = (x2 - L * math.cos(ang + 0.45), y2 - L * math.sin(ang + 0.45))
    draw.polygon([(x2, y2), head1, head2], fill=(22, 101, 52, 255))

