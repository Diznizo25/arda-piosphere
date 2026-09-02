"""Remove the dev/demo water source d6734528 (no name, ward or source_ref,
created 29 Aug during demo testing) so real herder confirmation lists near
Isiolo town don't offer a fictional point. Zones + build row cascade.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.db import get_pg_connection  # noqa: E402

WID = "d6734528-8e63-4c69-8d02-a6e5a004b0f5"

with get_pg_connection() as conn:
    with conn.cursor() as cur:
        cur.execute(
            "select count(*) n from pastoralists where water_source_id = %s", (WID,))
        if cur.fetchone()["n"]:
            print("aborting: referenced by a pastoralist")
            sys.exit(1)
        cur.execute("delete from water_point_builds where water_source_id = %s", (WID,))
        print("deleted builds:", cur.rowcount)
        cur.execute("delete from piosphere_zones where water_source_id = %s", (WID,))
        print("deleted zones:", cur.rowcount)
        cur.execute("delete from water_sources where id = %s", (WID,))
        print("deleted water source:", cur.rowcount)
    conn.commit()
print("done")
