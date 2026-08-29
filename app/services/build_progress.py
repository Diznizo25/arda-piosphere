"""Renders the "live progress bar" image the bot sends to a herder while their
pinned water point is being built (see water_point_builds / build tracker).

Pure Pillow, no network. The bar fills left-to-right as the build moves through
stages; the stage label is shown under the bar. Swahili/English labels are both
ASCII-safe (apostrophes only), so Pillow's bundled default font is fine.
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 640, 220
TITLE_SW = "MAENDELEO YA CHANZO CHA MAJI"
TITLE_EN = "WATER POINT BUILD PROGRESS"
_DONE_COLOR = (34, 197, 94)     # green
_RUNNING_COLOR = (59, 130, 246)  # blue

_STAGES_SW = {
    "zones": "Kuandaa maeneo ya kuzunguka",
    "compute": "Kupakua data ya satelaiti",
    "transfer": "Kusafirisha ramani ya malisho",
    "done": "Imeanza kutumika!",
    "failed": "Imekwama - tutajaribu tena",
}
_STAGES_EN = {
    "zones": "Preparing grazing zones",
    "compute": "Downloading satellite data",
    "transfer": "Uploading pasture map",
    "done": "Live!",
    "failed": "Stalled - will retry",
}


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # older Pillow without size kwarg
        return ImageFont.load_default()


def render_progress_bar(progress: int, stage_key: str, language: str = "swahili") -> bytes:
    """Return a PNG (bytes) of the progress bar at the given percent."""
    progress = max(0, min(100, int(progress)))
    title = TITLE_SW if language != "english" else TITLE_EN
    label = (_STAGES_SW if language != "english" else _STAGES_EN).get(
        stage_key, stage_key
    )
    fill_color = _DONE_COLOR if stage_key in ("done", "failed") else _RUNNING_COLOR

    img = Image.new("RGB", (WIDTH, HEIGHT), (18, 26, 36))
    draw = ImageDraw.Draw(img)
    font_t = _font(30)
    font_b = _font(24)
    font_p = _font(30)

    draw.text((WIDTH // 2, 28), title, font=font_t, fill=(226, 232, 240),
              anchor="mm")

    margin, bar_h, bar_y = 44, 46, 78
    bar_w = WIDTH - 2 * margin
    draw.rounded_rectangle([margin, bar_y, WIDTH - margin, bar_y + bar_h],
                           radius=bar_h // 2, fill=(40, 52, 68))
    fill_w = int(bar_w * progress / 100)
    if fill_w > 4:
        draw.rounded_rectangle([margin, bar_y, margin + fill_w, bar_y + bar_h],
                               radius=bar_h // 2, fill=fill_color)
    draw.text((WIDTH // 2, bar_y + bar_h // 2), f"{progress}%",
              font=font_p, fill=(255, 255, 255), anchor="mm")

    draw.text((WIDTH // 2, bar_y + bar_h + 42), label,
              font=font_b, fill=(148, 163, 184), anchor="mm")

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
