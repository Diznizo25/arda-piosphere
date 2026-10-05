"""Check MALISHO YA LEO against the LIVE service.

Run:  python scripts/check_graze_live.py [water_source_id]

Exercises POST /dev/graze in both modes — synthetic numbers (a dry-season walk, a
green flush, and a thin sward) so the ledger can be argued about against a season we
are not in, and real stored satellite data for a water point — and prints the exact
message a herder would receive, plus the bands behind it.

What to look for, and what would be a bug:
  * every reading carries its snapshot date, and one with no date says so;
  * "deficit" only when the WHOLE band is negative;
  * a green flush produces NO supplement options at all;
  * the first option, when there is one, is free;
  * a longer walk raises the locomotion term and can flip the verdict;
  * no drug, no dose, no diagnosis, no body-weight claim anywhere.

The debug key comes from WHATSAPP_VERIFY_TOKEN in .env (the same guard the other
/dev endpoints use), so nothing secret is ever printed.
"""
from __future__ import annotations

import json
import re
import sys

sys.path.insert(0, ".")

try:  # a Windows console is cp1252 and would crash printing the Swahili emoji
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402

FALLBACK_BASE = "https://arda-piosphere.onrender.com"

DRY_WALK = {"satvi": 0.30, "ndvi": 0.08, "bsi": 0.06, "ndmi": -0.05,
            "walk_km": 14, "species": "cattle", "head_count": 18,
            "temp_max_c": 38, "snapshot": "05 Sep", "from_manyatta": True,
            "better": {"satvi": 0.33, "ndvi": 0.45, "bsi": 0.06, "ndmi": 0.05,
                       "walk_km": 4, "direction": "Kaskazini-Mashariki"}}
GREEN_FLUSH = dict(DRY_WALK, satvi=0.34, ndvi=0.50, ndmi=0.06, walk_km=2,
                   temp_max_c=26, better=None)
THIN_SWARD = dict(DRY_WALK, satvi=0.08, ndvi=0.06, walk_km=16, better=None)


def _show(c: httpx.Client, base: str, headers: dict, label: str, body: dict) -> dict:
    r = c.post(f"{base}/dev/graze", json=body, headers=headers)
    print(f"\n-- {label} (HTTP {r.status_code}) --")
    if r.status_code != 200:
        print(r.text[:300])
        return {}
    out = r.json()
    q, led = out.get("quality", {}), out.get("ledger", {})
    print("quality:", q.get("condition"), "|", q.get("curing"),
          "| utilisable", q.get("utilisable_lo_kg_ha"), "-", q.get("utilisable_hi_kg_ha"),
          "kg/ha | snapshot:", q.get("snapshot_as_of"))
    print("ledger:", "walk", led.get("walk_km"), "km |",
          "required", led.get("required_mj"), "MJ |",
          "intake", led.get("intake_mj"), "MJ |",
          "balance", led.get("balance_mj"),
          f"[{led.get('balance_lo_mj')}..{led.get('balance_hi_mj')}]",
          "->", led.get("verdict"))
    print("options:", [(o["code"], o.get("cost_per_head_ksh")) for o in out.get("options", [])]
          or "NONE (nothing to buy — correct when gaining)")
    print(out.get("message_swa"))
    return out


def main() -> int:
    settings = get_settings()
    base = (settings.app_public_base_url or FALLBACK_BASE).rstrip("/")
    headers = {"X-Debug-Key": settings.whatsapp_verify_token}

    with httpx.Client(timeout=60.0) as c:
        r = c.get(f"{base}/health")
        print("health:", r.status_code, r.text[:80])

        dry = _show(c, base, headers, "dry season, long walk, hot day", DRY_WALK)
        flush = _show(c, base, headers, "green flush, short walk", GREEN_FLUSH)
        _show(c, base, headers, "thin sward, very long walk", THIN_SWARD)

        # The two invariants worth failing loudly on, live.
        d_led = dry.get("ledger", {})
        assert d_led.get("verdict") in ("deficit", "holding"), d_led
        assert not (flush.get("options") or []), \
            "a gaining herd must be offered nothing at all"
        for out in (dry, flush):
            for word in ("dawa", "sindano", "chanjo", "ugonjwa"):
                assert word not in (out.get("message_swa", "") + out.get("message_eng", "")
                                    + out.get("spoken_swa", "")).lower(), word
        print("\ninvariants: gaining -> no options; no drug words in any rendering OK")

        point = sys.argv[1] if len(sys.argv) > 1 else None

        # The thing the herder actually TAPS: the map page in graze mode must come
        # up WITH the pasture layer and the tap handler, or he is choosing where the
        # herd grazed on a blank street map.
        page = c.get(f"{base}/mapview/",
                     params={"lat": 0.5669, "lon": 37.2402, "species": "cattle",
                             "interval": "daily", "lang": "swa", "graze": 1,
                             "t": "live-check", **({"id": point} if point else {})})
        print(f"\n-- tap-the-map page (HTTP {page.status_code}) --")
        raw = re.search(r"const D = (\{.*?\});\n", page.text, re.S)
        data = json.loads(raw.group(1)) if raw else {}
        overlay = data.get("overlay") or {}
        print("graze mode:", data.get("graze"))
        print("pasture layer:", {k: overlay.get(k) for k in ("available", "usable_pct")})
        print("rings:", [r.get("species") for r in data.get("rings", [])])
        print("tap handler:", "D.graze.endpoint" in page.text,
              "| banner:", bool((data.get("text") or {}).get("g_hint")))
        assert data.get("graze", {}).get("on") is True, "graze mode did not reach the page"
        assert "D.graze.endpoint" in page.text, "the page cannot post the tapped spot"

        if point:
            img = c.get(f"{base}/map/{point}.png",
                        params={"lat": 0.5669, "lon": 37.2402, "species": "cattle",
                                "pasture": 1, "lang": "swa", "v": 9})
            print("map image (for the message header):", img.status_code,
                  img.headers.get("content-type"), len(img.content), "bytes")

            r = c.post(f"{base}/dev/graze",
                       json={"water_source_id": point, "lat": 0.5669, "lon": 37.2402,
                             "species": "cattle", "head_count": 18, "walk_km": 8},
                       headers=headers)
            print(f"\n-- real satellite data (HTTP {r.status_code}) --")
            if r.status_code != 200:
                # Report the failure honestly instead of raising on .json(): a live
                # check that crashes tells you less than one that prints the body.
                print("body:", r.text[:400])
                return 1
            body = r.json()
            print(json_summary(body))
            print(body.get("message_swa"))
        else:
            print("\n(pass a water_source_id to also read the real stored COG)")
    return 0


def json_summary(body: dict) -> str:
    if not body.get("ok"):
        return f"not ok: {body.get('error')}"
    q, led = body.get("quality", {}), body.get("ledger", {})
    return (f"{q.get('condition')} @ {q.get('snapshot_as_of')} | "
            f"balance {led.get('balance_mj')} MJ -> {led.get('verdict')}")


if __name__ == "__main__":
    raise SystemExit(main())
