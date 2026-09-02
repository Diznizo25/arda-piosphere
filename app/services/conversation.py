"""Persistent conversation state for guided WhatsApp flows.

WhatsApp webhooks are stateless, so multi-turn flows (onboarding, weight
measurement, water-point PIN confirmation) need a row per phone that says
"which step are we on and what has the herder told us so far".

Flows are resumed at the top of every inbound message; a herder can abandon a
flow at any time (e.g. just send their location) and the state is cleared.
"""
from __future__ import annotations

import json
import logging

from app.db import get_pg_connection

log = logging.getLogger(__name__)

GET_SQL = "select state, data from conversation_state where phone_number = %(phone)s"

UPSERT_SQL = """
insert into conversation_state (phone_number, state, data)
values (%(phone)s, %(state)s, %(data)s::jsonb)
on conflict (phone_number) do update set
    state = excluded.state,
    data = excluded.data,
    updated_at = now()
"""

CLEAR_SQL = "delete from conversation_state where phone_number = %(phone)s"


def get_state(phone: str) -> tuple[str | None, dict]:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(GET_SQL, {"phone": phone})
            row = cur.fetchone()
    if not row:
        return None, {}
    try:
        data = json.loads(row["data"]) if isinstance(row["data"], str) else (row["data"] or {})
    except Exception:  # noqa: BLE001
        data = {}
    return row["state"], data


def set_state(phone: str, state: str, data: dict | None = None) -> None:
    payload = json.dumps(data or {})
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(UPSERT_SQL, {"phone": phone, "state": state, "data": payload})
        conn.commit()


def clear_state(phone: str) -> None:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(CLEAR_SQL, {"phone": phone})
        conn.commit()


def state_age_seconds(phone: str) -> int | None:
    """Age of the conversation_state row in seconds (None if no row)."""
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """select extract(epoch from (now() - updated_at))::int as age
                   from conversation_state where phone_number = %(phone)s""",
                {"phone": phone},
            )
            row = cur.fetchone()
    return row["age"] if row else None
