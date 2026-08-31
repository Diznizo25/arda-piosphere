"""Check Render deploy status (uses RENDER_API_KEY from .env)."""
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
    print("no RENDER_API_KEY")
    sys.exit(1)
HEADERS = {"Authorization": f"Bearer {KEY}", "Accept": "application/json"}


def main() -> None:
    r = httpx.get("https://api.render.com/v1/services", headers=HEADERS, timeout=30)
    r.raise_for_status()
    services = [s["service"] for s in r.json()]
    web = next((s for s in services if s.get("type") == "web_service"), None)
    if not web:
        print("no web service")
        return
    dr = httpx.get(
        f"https://api.render.com/v1/services/{web['id']}/deploys?limit=6",
        headers=HEADERS, timeout=30,
    )
    if dr.status_code != 200:
        print("deploys err:", dr.status_code, dr.text[:200])
        return
    for d in dr.json():
        dep = d.get("deploy", {})
        commit = dep.get("commit", {}) or {}
        print(
            f"  commit={str(commit.get('id', '?'))[:8]} "
            f"status={dep.get('status')} trigger={dep.get('trigger')} "
            f"created={dep.get('createdAt')}"
        )


if __name__ == "__main__":
    main()
