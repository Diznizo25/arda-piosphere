"""Smoke-test the dashboard endpoints (fail-open: works even if DB is down)."""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

try:  # a Windows console is cp1252 and would crash printing the Swahili emoji
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

client = TestClient(app)
KEY = ("?key=" + get_settings().dashboard_token) if get_settings().dashboard_token else ""


def main() -> None:
    # Health
    r = client.get("/dashboard/api/health" + KEY)
    print("health:", r.status_code, "checks:", r.json().get("checks"))
    # Page
    r = client.get("/dashboard" + KEY)
    assert r.status_code == 200 and "Ops Dashboard" in r.text
    print("page:", r.status_code, len(r.text), "bytes")
    # Summary
    r = client.get("/dashboard/api/summary" + KEY)
    print("summary:", r.status_code)
    s = r.json()
    print("  water_sources:", s.get("water_sources", {}).get("total"),
          "pastoralists:", s.get("pastoralists", {}).get("total"),
          "cogs:", s.get("cogs"))
    # Water sources geojson
    r = client.get("/dashboard/api/water-sources" + KEY)
    gj = r.json()
    print("water-sources geojson:", r.status_code, "features:", len(gj.get("features", [])))
    if gj.get("features"):
        f = gj["features"][0]
        print("  sample:", f["properties"].get("ward"), f["properties"].get("build_status"),
              "cog:", f["properties"].get("has_cog"))
    # Activity
    r = client.get("/dashboard/api/activity" + KEY)
    print("activity:", r.status_code, "events:", len(r.json().get("events", [])))
    # Timeseries
    r = client.get("/dashboard/api/timeseries?days=14" + KEY.replace("?", "&"))
    ts = r.json()
    print("timeseries:", r.status_code, "labels:", len(ts.get("labels", [])), "kinds:", ts.get("kinds"))
    # COG stats + preview for the first source with a COG (or first source).
    # Slow locally (8MB overview from R2) — enable with --slow.
    if "--slow" in sys.argv:
        wsid = None
        for f in gj.get("features", []):
            if f["properties"].get("has_cog"):
                wsid = f["properties"]["id"]
                break
        if not wsid and gj.get("features"):
            wsid = gj["features"][0]["properties"]["id"]
        if wsid:
            r = client.get(f"/dashboard/api/cog/{wsid}/stats" + KEY)
            print("cog stats:", r.status_code, "available:", r.json().get("available"))
            if r.json().get("available"):
                print("  bands:", [b["band"] for b in r.json()["bands"]])
            r = client.get(f"/dashboard/api/cog/{wsid}/0.png" + KEY)
            print("cog preview NDVI:", r.status_code, r.headers.get("content-type"),
                  len(r.content), "bytes")
            assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    # Zones
    if gj.get("features"):
        zid = gj["features"][0]["properties"]["id"]
        r = client.get(f"/dashboard/api/zones/{zid}" + KEY)
        print("zones:", r.status_code, "features:", len(r.json().get("features", [])))
    # WhatsApp overview: who is talking to us + the exact menu they are offered
    r = client.get("/dashboard/api/whatsapp" + KEY)
    wa = r.json()
    print("whatsapp:", r.status_code, "degraded:", wa.get("degraded"),
          "totals:", wa.get("totals"))
    print("  herders:", len(wa.get("herders", [])),
          "messages:", len(wa.get("messages", [])))
    menu = wa.get("menu") or {}
    print("  menu: features:", menu.get("features"), "cap:", menu.get("row_limit"))
    for lang, info in (menu.get("languages") or {}).items():
        print(f"  menu {lang}: rows={info.get('count')} sections={info.get('sections')}")
        for row in (info.get("rows") or [])[:12]:
            print("    -", row["label"], "->", row["service"])
        if info.get("count", 99) > (menu.get("row_limit") or 99):
            raise AssertionError(f"{lang} menu exceeds the row cap")
    # Forecast receipts: what we said, and whether it came true
    r = client.get("/dashboard/api/forecasts" + KEY)
    fb = r.json()
    print("forecasts:", r.status_code, "available:", fb.get("available"),
          "detail:", fb.get("detail"))
    print("  receipts:", len(fb.get("receipts", [])), "rings:", len(fb.get("rings", [])),
          "scoreboard:", fb.get("scoreboard"))
    if fb.get("receipts"):
        first = fb["receipts"][0]
        print("  newest receipt:", first.get("issued_on"), first.get("water_point"),
              first.get("days_left"), "days, outcome:", first.get("outcome"))
    if fb.get("rings"):
        ring = fb["rings"][0]
        print("  newest ring:", ring.get("water_point"), ring.get("species"),
              "usable:", ring.get("usable_pct"), "near/far gap:", ring.get("gap_pp"))
    # Auth: set a token and confirm 401 without it
    prev = get_settings().dashboard_token
    get_settings().dashboard_token = "sekrit"
    r = client.get("/dashboard")
    assert r.status_code == 200, r.status_code   # the shell carries no data
    r = client.get("/dashboard/api/summary")
    assert r.status_code == 401, r.status_code
    r = client.get("/dashboard/api/whatsapp")
    assert r.status_code == 401, r.status_code
    r = client.get("/dashboard/api/summary?key=sekrit")
    assert r.status_code == 200, r.status_code
    get_settings().dashboard_token = prev
    print("auth guard OK (page public, data 401 without token, 200 with token)")
    print("\nDashboard smoke test passed.")


if __name__ == "__main__":
    main()
