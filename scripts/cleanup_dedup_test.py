"""Remove the +254712345678 webhook-dedup test rows."""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.db import get_pg_connection  # noqa: E402

with get_pg_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("delete from pastoralists where phone_number like '%712345678'")
        print("pastoralists deleted:", cur.rowcount)
        cur.execute("delete from query_log where phone like '%712345678'")
        print("query_log deleted:", cur.rowcount)
    conn.commit()
