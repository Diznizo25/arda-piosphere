"""Check the pest windows (and the service they ride on) against the LIVE service.

Run:  python scripts/check_pest_live.py

It exercises POST /dev/pest in both modes — synthetic numbers (so the thresholds can
be checked against a season we are not in) and real stored data for a water point —
and prints the exact message a herder would receive. The point is to see the wording
and the tiers as a herder would, before he does.

The debug key comes from WHATSAPP_VERIFY_TOKEN in .env (the same guard the other
/dev endpoints use), so nothing secret is ever printed.
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")

try:  # a Windows console is cp1252 and would crash printing the Swahili emoji
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402

FALLBACK_BASE = "https://arda-piosphere.onrender.com"


def main() -> int:
    settings = get_settings()
    base = (settings.app_public_base_url or FALLBACK_BASE).rstrip("/")
    headers = {"X-Debug-Key": settings.whatsapp_verify_token}

    with httpx.Client(timeout=45.0) as c:
        r = c.get(f"{base}/health")
        print("health:", r.status_code, r.text[:80])

        dry = {"rain": [0.0] * 30, "soil_moisture": 0.10, "temp_max_c": 34.0,
               "humidity": 25.0}
        body = c.post(f"{base}/dev/pest", json=dry, headers=headers).json()
        print("\n-- dry season --")
        print("tiers:", [w["tier"] for w in body.get("windows", [])],
              "| highest:", body.get("highest_tier"))
        print(body.get("message_swa"))
        print("weekly line:", body.get("weekly_line_swa"))

        onset = {"rain": [0.0] * 16 + [4.0, 6.0, 5.0, 8.0, 0.0, 0.0, 3.0],
                 "soil_moisture": 0.22, "temp_max_c": 24.0, "humidity": 70.0,
                 "ndmi": 0.05}
        body = c.post(f"{base}/dev/pest", json=onset, headers=headers).json()
        print("\n-- rain onset --")
        print("tiers:", [w["tier"] for w in body.get("windows", [])],
              "| highest:", body.get("highest_tier"))
        print(body.get("message_swa"))
        print("weekly line:", body.get("weekly_line_swa"))

        point = sys.argv[1] if len(sys.argv) > 1 else None
        if point:
            body = c.post(f"{base}/dev/pest", json={"water_source_id": point},
                          headers=headers).json()
            print(f"\n-- stored data for {point[:8]} --")
            print("series days:", body.get("series_days"),
                  "| highest:", body.get("highest_tier"))
            for w in body.get("windows", []):
                print("   ", w["key"], w["tier"], f"{w['score']}/"
                      f"{w['signals_available']}", w["evidence_swa"][:2])
        else:
            print("\n(pass a water_source_id to also check the real stored series)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
