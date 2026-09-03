"""Syntax-check the inline script of the LOCAL template."""
from __future__ import annotations

import json
import re
import subprocess
import sys

sys.path.insert(0, ".")
from app.routers import mapview  # noqa: E402

data = {
    "lang": "swa", "title": "x", "tap": "t", "you": "u", "herder": {"lon": 37.58, "lat": 0.35},
    "options": [], "rings": [], "species": "cattle", "interval": "daily",
    "main_id": None, "overlay": None, "eff_km": None,
    "text": {"mapBase": "Map", "satBase": "Satellite"}, "focus": 1,
}
html = mapview._PAGE_TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
m = re.search(r"<script>\n(.*)</script>", html, re.S)
js = m.group(1)
open("_tmp_page.js", "w", encoding="utf-8").write(js)
p = subprocess.run(["node", "--check", "_tmp_page.js"], capture_output=True, text=True)
print("node --check:", p.returncode)
print(p.stderr[:1500] or "JS syntax OK")
sys.exit(p.returncode)
