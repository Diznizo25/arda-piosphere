"""Trigger a Render deploy of main (reads RENDER_API_KEY from .env)."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

KEY = None
for line in Path(".env").read_text(encoding="utf-8").splitlines():
    if line.startswith("RENDER_API_KEY="):
        KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
        break
if not KEY:
    print("RENDER_API_KEY not found")
    sys.exit(1)

HEADERS = {"Authorization": f"Bearer {KEY}", "Accept": "application/json"}


def main() -> int:
    r = httpx.get("https://api.render.com/v1/services", headers=HEADERS, timeout=30)
    r.raise_for_status()
    services = [s["service"] for s in r.json()]
    web = next((s for s in services if s.get("type") == "web_service"), None)
    if not web:
        print("no web service found")
        return 1
    svc_id = web["id"]
    dr = httpx.post(
        f"https://api.render.com/v1/services/{svc_id}/deploys",
        headers=HEADERS,
        json={"branch": "main", "clearCache": "do_not_clear"},
        timeout=30,
    )
    print("deploy POST status:", dr.status_code)
    print(dr.text[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())
