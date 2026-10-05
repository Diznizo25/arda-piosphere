"""See the mentor layer against the LIVE service: the data text, and the rewrite.

Run:  python scripts/check_mentor_live.py [water_source_id]

For each service it prints the deterministic message and the mentor version side by
side, plus whether the rewrite was actually used and the guard's reason when it was
not. This is how the wording gets reviewed as WRITING — the thing a herder reads —
instead of as template strings in a file.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

try:  # a Windows console is cp1252 and would crash printing the emoji
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


def _mentor(c: httpx.Client, base_url: str, headers: dict, kind: str, text: str,
            facts: dict | None = None, lang: str = "swa") -> dict:
    r = c.post(f"{base_url}/dev/mentor",
               json={"kind": kind, "text": text, "facts": facts or {}, "lang": lang},
               headers=headers)
    return r.json() if r.status_code == 200 else {"ok": False, "error": r.text[:200]}


def show(label: str, base: str, out: str, changed: bool, guard: str) -> None:
    print(f"\n{'=' * 78}\n{label}   (mentor used: {changed} | guard: {guard})\n{'=' * 78}")
    print("--- what the RULES compose (sent if the rewrite is rejected) ---")
    print(base)
    print("--- what the MENTOR says ---")
    print(out)


def main() -> int:
    settings = get_settings()
    base_url = (settings.app_public_base_url or FALLBACK_BASE).rstrip("/")
    headers = {"X-Debug-Key": settings.whatsapp_verify_token}
    point = sys.argv[1] if len(sys.argv) > 1 else None

    with httpx.Client(timeout=90.0) as c:
        print("health:", c.get(f"{base_url}/health").status_code)

        # 1) the grazing ledger, from the real deterministic engine.
        for lang, label in (("swa", "GRAZING LEDGER — Swahili"), ("eng", "GRAZING LEDGER — English")):
            body = c.post(f"{base_url}/dev/graze", json=DRY_WALK, headers=headers).json()
            data = body.get(f"message_{lang}") or ""
            if not data:
                print(f"{label}: no base text ({body.get('error')})")
                continue
            res = _mentor(c, base_url, headers, "graze", data, facts=body.get("ledger"),
                          lang=lang)
            show(label, data, res.get("mentor", ""), bool(res.get("changed")),
                 str(res.get("guard")))

        # 2) the pest windows, built from stored data for a real point.
        if point:
            res = c.post(f"{base_url}/dev/mentor",
                         json={"kind": "pest", "water_source_id": point, "lang": "swa"},
                         headers=headers).json()
            if res.get("ok"):
                show("PEST WINDOWS — Swahili", res.get("base", ""), res.get("mentor", ""),
                     bool(res.get("changed")), str(res.get("guard")))
            else:
                print("\npest:", res.get("error"))
        else:
            print("\n(pass a water_source_id to also see the pest message)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
