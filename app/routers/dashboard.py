"""Live ops dashboard: KPIs, water-point map, query monitor, COG explorer, the
WhatsApp conversation and the forecast receipts.

The HTML shell is public (it contains no data, only the code that asks for it), so
the link can be shared. With DASHBOARD_TOKEN unset the whole dashboard is open —
no key to type at all; with it set, every data endpoint needs it (?key=<token> or
Authorization: Bearer <token>) and the shell shows a one-line key bar instead of
blocking the page. Every data endpoint is fail-open (returns partial JSON rather
than 500ing) so the dashboard stays up even when a dependency is degraded.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import get_settings
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "dashboard.html"


def _auth_ok(key: str | None, authorization: str | None) -> bool:
    token = get_settings().dashboard_token
    if not token:
        return True
    if key and key == token:
        return True
    if authorization and authorization.lower().startswith("bearer "):
        if authorization[7:].strip() == token:
            return True
    return False


def require_auth(key: str | None = Query(default=None),
                 authorization: str | None = Header(default=None)) -> None:
    if not _auth_ok(key, authorization):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _json(obj, status: int = 200):
    return JSONResponse(status_code=status, content=obj)


@router.get("", response_class=HTMLResponse)
def dashboard_page() -> HTMLResponse:
    """The shell. No data in it, so it needs no key; the key is asked for once and
    kept in localStorage, and every /api/* call below still checks it."""
    if not TEMPLATE.exists():
        raise HTTPException(status_code=500, detail="dashboard template missing")
    html = TEMPLATE.read_text(encoding="utf-8")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@router.get("/api/health", dependencies=[Depends(require_auth)])
def api_health():
    return _json(dashboard_service.system_health())


@router.get("/api/freshness", dependencies=[Depends(require_auth)])
def api_freshness():
    """How old the data behind an answer is: satellite snapshot, rain series,
    forecast cache, climatology and the herder-report loop, each compared with the
    cadence it is supposed to have."""
    return _json(dashboard_service.data_freshness())


@router.get("/api/summary", dependencies=[Depends(require_auth)])
def api_summary():
    return _json(dashboard_service.summary())


@router.get("/api/water-sources", dependencies=[Depends(require_auth)])
def api_water_sources():
    return _json(dashboard_service.water_sources_geojson())


@router.get("/api/zones/{water_source_id}", dependencies=[Depends(require_auth)])
def api_zones(water_source_id: str):
    return _json(dashboard_service.zones_geojson(water_source_id))


@router.get("/api/activity", dependencies=[Depends(require_auth)])
def api_activity(limit: int = Query(default=25, ge=1, le=100)):
    return _json(dashboard_service.activity(limit))


@router.get("/api/timeseries", dependencies=[Depends(require_auth)])
def api_timeseries(days: int = Query(default=14, ge=1, le=90)):
    return _json(dashboard_service.timeseries(days))


@router.get("/api/cog/{water_source_id}/stats", dependencies=[Depends(require_auth)])
def api_cog_stats(water_source_id: str):
    return _json(dashboard_service.cog_stats(water_source_id))


@router.get("/api/cog/{water_source_id}/{band}.png", dependencies=[Depends(require_auth)])
def api_cog_preview(water_source_id: str, band: int = 0):
    if band < 0 or band > 7:
        raise HTTPException(status_code=400, detail="band must be 0..7")
    png = dashboard_service.cog_preview_png(water_source_id, band)
    if png is None:
        raise HTTPException(status_code=404, detail="COG preview not available")
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=300"})


@router.get("/api/whatsapp", dependencies=[Depends(require_auth)])
def api_whatsapp(limit: int = Query(default=20, ge=1, le=100)):
    """Who is talking to us, what they last said, and the exact menu they see."""
    return _json(dashboard_service.whatsapp_overview(limit))


@router.get("/api/forecasts", dependencies=[Depends(require_auth)])
def api_forecasts(limit: int = Query(default=25, ge=1, le=200)):
    """Forecast receipts, the backtest scoreboard and the latest ring readings."""
    return _json(dashboard_service.forecast_board(limit))
