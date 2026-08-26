"""Map image endpoints.

GET /map/{water_source_id}.png renders the species rings for that water point
as a PNG (used by the WhatsApp flow to send herders a map of their rings, and
by anyone testing the renderer over HTTP).
"""
from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, HTTPException, Response

from app.services.map_renderer import render_rings_png

router = APIRouter(prefix="/map", tags=["map"])

CACHE_TTL_SECONDS = 3600  # rings only change when zones are regenerated


@lru_cache(maxsize=64)
def _render_cached(water_source_id: str) -> bytes:
    return render_rings_png(water_source_id)


@router.get("/{water_source_id}.png")
def get_rings_map(water_source_id: str) -> Response:
    try:
        png = _render_cached(water_source_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Map render failed: {e}")
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": f"public, max-age={CACHE_TTL_SECONDS}"},
    )
