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


@lru_cache(maxsize=16)
def _render_cached(water_source_id: str, herder_lon: float | None, herder_lat: float | None,
                   version: int, species: str | None, pasture: bool, lang: str,
                   confirm_source_id: str | None, numbered_ids: tuple[str, ...],
                   fit_view: bool) -> bytes:
    from app.services import water_sources

    numbered_sources = None
    if numbered_ids:
        try:
            by_id = {w.id: w for w in water_sources.list_water_sources()}
            numbered_sources = [
                {"water_source_id": wid, "lon": by_id[wid].lon, "lat": by_id[wid].lat,
                 "name": by_id[wid].name, "water_type": by_id[wid].water_type,
                 "ward": by_id[wid].ward}
                for wid in numbered_ids if wid in by_id
            ]
        except Exception:  # noqa: BLE001
            numbered_sources = None
    return render_rings_png(water_source_id, herder_lon, herder_lat,
                            species=species, pasture=pasture, lang=lang,
                            confirm_source_id=confirm_source_id,
                            numbered_sources=numbered_sources, fit_view=fit_view)


@router.get("/{water_source_id}.png")
def get_rings_map(
    water_source_id: str,
    lat: float | None = Query(default=None, ge=-90, le=90),
    lon: float | None = Query(default=None, ge=-180, le=180),
    species: str | None = Query(default=None, pattern="^(cattle|shoat|camel)$"),
    pasture: bool = Query(default=True, description="Show the satellite pasture layer."),
    lang: str = Query(default="swa", pattern="^(swa|eng)$"),
    confirm: str | None = Query(default=None, description="Herder's confirmed water source uuid"),
    numbered: str | None = Query(default=None,
                                 description="Comma-separated water source uuids to draw numbered 1..N"),
    fit: int = Query(default=0,
                     description="fit=1: zoom out to show ALL numbered markers + the herder "
                                 "(confirmation 'options' map, no rings)"),
    v: int = Query(default=1, description="Cache-buster; bump when the renderer changes."),
) -> Response:
    """Render the species rings for a water point.

    Optional `lat`/`lon` query params mark the herder's position on the map
    (blue "Wewe hapa / You are here" pin + distance to the water) and center
    the view on them. `species` zooms to that species' ring, and `pasture=1`
    (default) colours the map by satellite forage quality (green=dry-forage/
    grass, red=bare) with a green arrow to the nearest good pasture patch and a
    big direction banner in the herder's language. `confirm` highlights the
    herder's confirmed water point; `numbered` draws the given water points as
    numbered 1..N markers (used by the water-point confirmation flow).
    """
    numbered_ids = tuple(u for u in (numbered or "").split(",") if u)
    try:
        from app.services import query_log

        t = query_log.timer()
        png = _render_cached(water_source_id, lon, lat, v, species, pasture, lang,
                             confirm, numbered_ids, bool(fit))
        query_log.log_query(kind="map", lat=lat, lon=lon, species=species,
                            water_source_id=water_source_id, result="ok", latency_ms=t.ms())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:  # noqa: BLE001
        try:
            from app.services import query_log

            query_log.log_query(kind="map", lat=lat, lon=lon, species=species,
                                water_source_id=water_source_id, result="error")
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(status_code=500, detail=f"Map render failed: {e}")
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=600"},
    )
