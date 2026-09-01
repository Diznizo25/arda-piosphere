"""Check query_log rows after an advisory call."""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.db import get_pg_connection

with get_pg_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("select kind, species, result, latency_ms, water_source_id "
                    "from query_log order by created_at desc limit 5")
        rows = cur.fetchall()
print(f"{len(rows)} query_log rows")
for r in rows:
    print(" ", dict(r))
