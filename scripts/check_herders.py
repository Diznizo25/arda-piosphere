"""Inspect the registered herders' state (for diagnosing missing replies)."""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.db import get_pg_connection  # noqa: E402

with get_pg_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("""
            select p.phone_number, p.preferred_language, p.primary_species,
                   p.full_name, p.onboarded_at is not null as onboarded,
                   p.water_source_id, p.voice_replies,
                   st_x(p.last_known_location) as lon, st_y(p.last_known_location) as lat,
                   p.updated_at
            from pastoralists p order by p.updated_at desc
        """)
        for r in cur.fetchall():
            print(dict(r))
