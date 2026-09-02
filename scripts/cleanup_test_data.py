"""Clean up test artifacts (fake pastoralist + inbound test rows)."""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.db import get_pg_connection  # noqa: E402

with get_pg_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("delete from pastoralists where phone_number like '+254700000%' "
                    "or phone_number like '+000%'")
        print("deleted test pastoralists:", cur.rowcount)
        cur.execute("delete from query_log where phone like '+254700000%' "
                    "or phone like '+000%'")
        print("deleted test query_log rows:", cur.rowcount)
    conn.commit()
