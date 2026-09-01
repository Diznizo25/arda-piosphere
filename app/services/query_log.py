"""Append-only query/activity log for the ops dashboard.

Writes to `query_log` (migration 004). Every function here is fail-open: a
database hiccup or missing table must NEVER break the advisory/map path it is
called from — we log the exception and move on.
"""
from __future__ import annotations

import json
import logging
import time

from app.db import get_pg_connection

log = logging.getLogger(__name__)

INSERT_SQL = """
insert into query_log (kind, phone, latitude, longitude, species, water_source_id, result, latency_ms, detail)
values (%(kind)s, %(phone)s, %(latitude)s, %(longitude)s, %(species)s, %(water_source_id)s,
        %(result)s, %(latency_ms)s, %(detail)s)
"""


def log_query(
    kind: str = "advisory",
    phone: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    species: str | None = None,
    water_source_id: str | None = None,
    result: str = "ok",
    latency_ms: int | None = None,
    detail: dict | None = None,
) -> None:
    """Insert one activity row. Never raises."""
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    INSERT_SQL,
                    {
                        "kind": kind,
                        "phone": phone,
                        "latitude": lat,
                        "longitude": lon,
                        "species": species,
                        "water_source_id": water_source_id,
                        "result": result,
                        "latency_ms": latency_ms,
                        "detail": json.dumps(detail or {}),  # psycopg can't adapt dict -> jsonb
                    },
                )
            conn.commit()
    except Exception:  # noqa: BLE001
        log.debug("query_log write failed (non-fatal):", exc_info=True)


class timer:
    """Simple context manager timing helper (avoids pulling in a dep)."""

    __slots__ = ("started",)

    def __init__(self) -> None:
        self.started = time.monotonic()

    def ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)
