"""Map image endpoints.

GET /map/{water_source_id}.png renders the species rings for that water point
as a PNG (used by the WhatsApp flow to send herders a map of their rings, and
by anyone testing the renderer over HTTP).
"""
from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query, Response

from app.services.map_renderer import render_rings_png

router = APIRouter(prefix="/map", tags=["map"])

CACHE_TTL_SECONDS = 3600  # rings only change when zones are regenerated


@lru_cache(maxsize=64)
def _render_cached(water_source_id: str, herder_lon: float | None, herder_lat: float | None,
                   version: int, species: str | None, pasture: bool, lang: str) -> bytes:
    return render_rings_png(water_source_id, herder_lon, herder_lat,
                            species=species, pasture=pasture, lang=lang)


@router.get("/{water_source_id}.png")
def get_rings_map(
    water_source_id: str,
    lat: float | None = Query(default=None, ge=-90, le=90),
    lon: float | None = Query(default=None, ge=-180, le=180),
    species: str | None = Query(default=None, pattern="^(cattle|shoat|camel)$"),
    pasture: bool = Query(default=True, description="Show the satellite pasture layer."),
    lang: str = Query(default="swa", pattern="^(swa|eng)$"),
    v: int = Query(default=1, description="Cache-buster; bump when the renderer changes."),
) -> Response:
    """Render the species rings for a water point.

    Optional `lat`/`lon` query params mark the herder's position on the map
    (blue "Wewe hapa / You are here" pin + distance to the water) and center
    the view on them. `species` zooms to that species' ring, and `pasture=1`
    (default) colours the map by satellite forage quality (green=dry-forage/
    grass, red=bare) with a green arrow to the nearest good pasture patch and a
    big direction banner in the herder's language.
    """
    try:
        png = _render_cached(water_source_id, lon, lat, v, species, pasture, lang)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Map render failed: {e}")
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=600"},
    )
