"""
Build a standalone preview of the operations dashboard, with sample data baked in.

Why this exists: the dashboard needs a live Postgres, R2 and a service token to
render anything, so nobody can review a design change without deploying it. This
reads the REAL template — one source of truth, so the preview cannot drift from
what ships — replaces the `fetch` layer with fixed sample payloads, and writes a
single self-contained HTML file you can open locally.

The sample numbers are deliberately unflattering: one stale source, a failed
pipeline, a non-zero error rate. A dashboard that is only ever reviewed against
healthy data hides exactly the states it exists to surface.

    python scripts/build_dashboard_preview.py
    # -> build/dashboard_preview.html
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "app" / "templates" / "dashboard.html"
OUT = ROOT / "build" / "dashboard_preview.html"

NOW = datetime.now(timezone.utc)
DAY = 86400


def iso(seconds_ago: float) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).isoformat()


def sample() -> dict[str, object]:
    """Payloads shaped exactly like app/services/dashboard_service.py returns."""

    # A mix of states on purpose: satellite overdue, forecast stale, one never received.
    sources = [
        ("satellite", "Satellite indices", "Sentinel-2 · every 14 days",
         14 * DAY, 18.4 * DAY, "7/9 water points built · missing: Kipsing, Ewaso"),
        ("rain_observed", "Observed rainfall", "Open-Meteo · daily",
         2 * DAY, 0.7 * DAY, "4,182 rows"),
        ("rain_forecast", "Rain forecast", "Open-Meteo · 16-day, daily refresh",
         2 * DAY, 5.1 * DAY, "144 rows"),
        ("climatology", "Rain climatology", "CHIRPS · 30-yr normals, yearly",
         400 * DAY, 96 * DAY, "108 rows"),
        ("herder_reports", "Herder reports", "continuous · from WhatsApp",
         7 * DAY, None, "0 rows · status set on 0 points"),
    ]

    def status(age, expected):
        if age is None:
            return "missing"
        if age <= expected:
            return "fresh"
        return "aging" if age <= expected * 2 else "stale"

    freshness = {
        "generated_at": iso(0),
        "sources": [
            {"key": k, "label": lbl, "detail": det,
             "newest": None if age is None else iso(age),
             "age_seconds": None if age is None else int(age),
             "expected_seconds": int(exp),
             "status": status(age, exp), "extra": extra}
            for k, lbl, det, exp, age, extra in sources
        ],
        "satellite": {"built": 7, "total": 9, "oldest": iso(18.4 * DAY),
                      "oldest_age_seconds": int(18.4 * DAY)},
        "pipelines": [
            {"name": "refresh-indices", "conclusion": "success", "at": iso(3.2 * DAY),
             "detail": "9 points · 41 min"},
            {"name": "build-water-points", "conclusion": "success", "at": iso(0.02 * DAY),
             "detail": "nothing pending"},
            {"name": "refresh-environment", "conclusion": "failure", "at": iso(0.7 * DAY),
             "detail": "Open-Meteo 429 — rate limited"},
            {"name": "weekly-note", "conclusion": "success", "at": iso(2.1 * DAY),
             "detail": "12 herders notified"},
            {"name": "keep-alive", "conclusion": "success", "at": iso(0.01 * DAY),
             "detail": ""},
        ],
    }

    summary = {
        "water_sources": {"total": 9, "with_zones": 9, "zones_total": 27,
                          "by_type": {"borehole": 4, "river": 2, "pan": 2, "well": 1},
                          "by_ward": {"Oldonyiro": 9}},
        "pastoralists": {"total": 14, "onboarded": 11,
                         "by_language": {"swahili": 12, "english": 2},
                         "by_species": {"cattle": 7, "shoat": 4, "camel": 3}},
        "builds": {"by_status": {"built": 7, "failed": 1, "pending": 1},
                   "last_updated": iso(0.02 * DAY)},
        "ground_truth": {"total": 23, "last_14d": 6,
                         "by_type": {"water_available": 9, "water_dry": 6,
                                     "pasture_poor": 4, "water_broken": 3,
                                     "pasture_good": 1}},
        "weights": {"records": 38, "herd_estimates": 9},
        "queries": {"total": 1043, "today": 37, "last_7d": 262,
                    "errors_7d": 6, "avg_latency_7d": 2840},
    }

    kinds = ["advisory", "map", "other", "weight", "status", "pin"]
    weights = [14, 7, 9, 3, 2, 1]
    labels, rows = [], []
    for d in range(13, -1, -1):
        day = (NOW - timedelta(days=d)).date().isoformat()
        labels.append(day)
        # A believable weekly rhythm plus one quiet day, so the chart is not flat.
        scale = 0.45 if d == 6 else (1.35 if d in (2, 9) else 1.0)
        rows.append({k: max(0, round(w * scale * (0.7 + 0.6 * ((d * 7 + i) % 5) / 4)))
                     for i, (k, w) in enumerate(zip(kinds, weights))})

    water_points = [
        ("Oldonyiro borehole", "borehole", 37.32, 0.73, "built", True, "functional"),
        ("Ewaso Ng'iro", "river", 37.55, 0.62, "built", True, "flowing"),
        ("Kipsing pan", "pan", 37.18, 0.86, "failed", False, "dry"),
        ("Lengetia well", "well", 37.41, 0.55, "built", True, "functional"),
        ("Ngare Ndare", "river", 37.62, 0.48, "built", True, "unknown"),
        ("Attan borehole", "borehole", 37.25, 0.94, "built", True, "broken"),
        ("Sipili pan", "pan", 37.48, 0.81, "pending", False, "unknown"),
        ("Chumvi borehole", "borehole", 37.37, 0.41, "built", True, "functional"),
        ("Naibor borehole", "borehole", 37.51, 0.90, "built", True, "intermittent"),
    ]
    features = [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [lon, lat]},
         "properties": {"id": f"ws-{i:04d}", "name": name, "water_type": wt,
                        "source_type": "osm", "ward": "Oldonyiro",
                        "build_status": bs, "has_cog": cog, "status": st}}
        for i, (name, wt, lon, lat, bs, cog, st) in enumerate(water_points, 1)
    ]

    events = [
        {"type": "query", "kind": "advisory", "result": "ok", "species": "cattle",
         "phone": "…8821", "latency_ms": 2140, "at": iso(240)},
        {"type": "query", "kind": "other", "result": "ok",
         "detail_text": "mvua itanyesha lini?", "phone": "…4417", "at": iso(900)},
        {"type": "report", "report_type": "water_broken",
         "text": "pampu imeharibika", "at": iso(1500)},
        {"type": "query", "kind": "map", "result": "ok", "species": "camel",
         "phone": "…9002", "latency_ms": 5310, "at": iso(2600)},
        {"type": "build", "status": "failed", "stage": "transfer to R2",
         "progress": 60, "at": iso(4100)},
        {"type": "query", "kind": "advisory", "result": "error",
         "species": "shoat", "phone": "…1173", "latency_ms": 9800, "at": iso(5200)},
        {"type": "query", "kind": "weight", "result": "ok", "phone": "…8821", "at": iso(7400)},
        {"type": "report", "report_type": "water_available",
         "text": "maji yapo", "at": iso(9100)},
        {"type": "query", "kind": "pin", "result": "ok", "phone": "…3308", "at": iso(12800)},
        {"type": "build", "status": "built", "stage": "overview", "progress": 100, "at": iso(15400)},
    ]

    bands = ["NDVI", "NDRE", "SATVI", "BSI", "NDMI", "NDWI", "VCI", "GSW_MONTHLY_RECURRENCE"]
    stats = {"available": True, "water_source_id": "ws-0001", "bands": [
        {"band": b,
         "mean": m, "min": lo, "max": hi, "std": sd,
         "valid_pixels": vp, "total_pixels": 262144}
        for b, m, lo, hi, sd, vp in [
            ("NDVI", 0.183, -0.041, 0.612, 0.074, 171_004),
            ("NDRE", 0.121, -0.030, 0.408, 0.051, 171_004),
            ("SATVI", 0.216, -0.062, 0.549, 0.088, 171_004),
            ("BSI", 0.148, -0.211, 0.472, 0.096, 171_004),
            ("NDMI", -0.042, -0.318, 0.301, 0.067, 171_004),
            ("NDWI", -0.287, -0.502, 0.341, 0.058, 171_004),
            ("VCI", 41.7, 0.0, 100.0, 22.4, 168_221),
            ("GSW_MONTHLY_RECURRENCE", 8.3, 0.0, 94.0, 19.1, 34_912),
        ]
    ]}
    # keep `bands` referenced so the band order above stays documented
    assert [b["band"] for b in stats["bands"]] == bands

    return {
        "/dashboard/api/health": {
            "db_ok": True, "r2_ok": True, "r2_objects": 18,
            "commit": "3e39157ab", "uptime_seconds": 61_400, "degraded": False,
            "instance": "srv-preview",
        },
        "/dashboard/api/freshness": freshness,
        "/dashboard/api/summary": summary,
        "/dashboard/api/water-sources": {"type": "FeatureCollection", "features": features},
        "/dashboard/api/activity": {"events": events},
        "/dashboard/api/timeseries": {"labels": labels, "kinds": kinds, "queries": rows},
        "/dashboard/api/cog/stats": stats,
    }


BANNER = """
<div style="background:#2A241B;border-bottom:1px solid #342C21;padding:9px 18px;
            font-family:'JetBrains Mono',monospace;font-size:11.5px;color:#D98A4A;
            text-align:center;letter-spacing:.04em">
  PREVIEW · sample data, not live · regenerate with scripts/build_dashboard_preview.py
</div>
"""

# Replaces the network layer only. Every render function, all CSS and all markup
# are the shipping ones, so what you review is what deploys.
STUB = """
/* ---- PREVIEW STUB: fixed payloads instead of fetch ---- */
const SAMPLE = __SAMPLE__;
async function getJSON(p) {
  const base = p.split('?')[0];
  if (/\\/api\\/cog\\/.*\\/stats$/.test(base)) return SAMPLE['/dashboard/api/cog/stats'];
  if (/\\/api\\/zones\\//.test(base)) {
    return { type:'FeatureCollection', features: [] };
  }
  if (SAMPLE[base]) return SAMPLE[base];
  throw new Error('no sample for ' + base);
}
"""


def main() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")

    # 1. Swap the real getJSON for the stub (keep `api()` — the COG <img> still uses it).
    pattern = re.compile(
        r"async function getJSON\(p\) \{.*?\n\}\n", re.S)
    if not pattern.search(html):
        raise SystemExit("getJSON() not found — did the template change shape?")
    stub = STUB.replace("__SAMPLE__", json.dumps(sample(), indent=1))
    # A callable replacement: re.sub would otherwise read the JSON's \u escapes
    # as regex escapes and blow up on the first non-ASCII character.
    html = pattern.sub(lambda _m: stub, html, count=1)

    # 2. Band previews are real PNGs from R2; there is nothing to show offline.
    html = html.replace(
        "document.getElementById('cogImg').src = api(`/dashboard/api/cog/${id}/${band}.png`);",
        "document.getElementById('cogImg').replaceWith(Object.assign("
        "document.createElement('div'), { id:'cogImg', className:'empty', "
        "style:'border:1px solid var(--rule);display:grid;place-items:center;"
        "aspect-ratio:1;background:var(--raised)', "
        "textContent:'band preview needs live R2' }));")

    # 3. No polling in a static file.
    html = html.replace("setInterval(refreshAll, 30000);", "/* no polling in preview */")

    # 4. Mark it unmistakably.
    html = html.replace("<body>\n", "<body>\n" + BANNER, 1)
    html = html.replace("<title>Arda Link · Operations</title>",
                        "<title>Arda Link · Operations (preview)</title>", 1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}  ({len(html) / 1024:.0f} KB)")
    print("open it directly in a browser — no server, no credentials needed")


if __name__ == "__main__":
    main()
