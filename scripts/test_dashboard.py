"""Smoke-test the dashboard endpoints (fail-open: works even if DB is down)."""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

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
    # Auth: set a token and confirm 401 without it
    prev = get_settings().dashboard_token
    get_settings().dashboard_token = "sekrit"
    r = client.get("/dashboard/api/summary")
    assert r.status_code == 401, r.status_code
    r = client.get("/dashboard/api/summary?key=sekrit")
    assert r.status_code == 200, r.status_code
    get_settings().dashboard_token = prev
    print("auth guard OK (401 without token, 200 with token)")
    print("\nDashboard smoke test passed.")


if __name__ == "__main__":
    main()
