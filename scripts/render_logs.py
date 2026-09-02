"""Fetch recent Render service logs and surface errors/tracebacks."""
from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()
key = os.getenv("RENDER_API_KEY")
SERVICE_ID = "srv-d9nmbkm1egvs738cjqe0"


def main() -> None:
    r = httpx.get(
        f"https://api.render.com/v1/services/{SERVICE_ID}/logs?limit=300",
        headers={"Authorization": f"Bearer {key}"},
        timeout=60,
    )
    print("status:", r.status_code)
    if r.status_code != 200:
        print(r.text[:1000])
        return
    data = r.json()
    lines = []
    for item in data:
        text = item.get("text") or item.get("message") or ""
        lines.append(text)
    joined = "\n".join(lines)
    print(f"log lines: {len(lines)}")
    # Surface errors + the last 60 lines overall
    bad = [l for l in lines if any(k in l.lower() for k in
            ("error", "traceback", "exception", "failed", "500", "crash", "restart"))]
    print(f"\n=== error-ish lines ({len(bad)}) ===")
    for l in bad[-40:]:
        print(l[:500])
    print("\n=== last 50 lines ===")
    for l in lines[-50:]:
        print(l[:300])


if __name__ == "__main__":
    main()
