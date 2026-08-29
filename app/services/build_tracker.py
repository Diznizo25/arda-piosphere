"""Water-point build tracking.

When a herder pins a water point, the WhatsApp handler registers the row +
species rings and inserts a build record here with status='pending'. A scheduled
GitHub Actions job (build-water-points.yml -> scripts/build_water_point.py)
claims pending builds, runs the single-point GEE compute -> R2 transfer
pipeline, and calls update_build() at each stage. The web service sends the
herder a rendered progress-bar image on every stage change (see
app/services/build_progress.py + the /dev/notify endpoint), so the herder is
never left hanging after the initial ack.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.db import get_pg_connection

log = logging.getLogger(__name__)

START_BUILD_SQL = """
insert into water_point_builds (water_source_id, creator_phone, language, status)
values (%(water_source_id)s, %(phone)s, %(language)s, 'pending')
on conflict (water_source_id) do nothing
returning id
"""

UPDATE_BUILD_SQL = """
update water_point_builds
set status = %(status)s,
    stage = %(stage)s,
    stage_index = %(stage_index)s,
    stage_total = %(stage_total)s,
    progress = %(progress)s,
    error = %(error)s
where water_source_id = %(water_source_id)s
"""

GET_BUILD_SQL = """
select id, water_source_id, creator_phone, language, status, stage,
       stage_index, stage_total, progress, error, updated_at
from water_point_builds
where water_source_id = %(water_source_id)s
"""

GET_BUILD_FOR_PHONE_SQL = """
select id, water_source_id, creator_phone, language, status, stage,
       stage_index, stage_total, progress, error, updated_at
from water_point_builds
where creator_phone = %(phone)s
order by created_at desc
limit 1
"""

CLAIM_PENDING_SQL = """
select b.id, b.water_source_id, b.creator_phone, b.language, b.status,
       b.stage, b.stage_index, b.stage_total, b.progress, b.error, b.updated_at
from water_point_builds b
join water_sources ws on ws.id = b.water_source_id
where b.status = 'pending'
order by b.created_at asc
for update skip locked
"""


@dataclass
class BuildRecord:
    id: str
    water_source_id: str
    creator_phone: str
    language: str
    status: str
    stage: str | None
    stage_index: int | None
    stage_total: int | None
    progress: int
    error: str | None
    updated_at: object | None = None


def _from_row(row) -> BuildRecord:
    return BuildRecord(
        id=str(row["id"]),
        water_source_id=str(row["water_source_id"]),
        creator_phone=row["creator_phone"],
        language=row["language"],
        status=row["status"],
        stage=row["stage"],
        stage_index=row["stage_index"],
        stage_total=row["stage_total"],
        progress=int(row["progress"]),
        error=row["error"],
        updated_at=row["updated_at"],
    )


def start_build(water_source_id: str, phone: str, language: str = "swahili") -> bool:
    """Insert a pending build record. Returns True if a new record was created
    (or already existed), False on any DB error — callers should fail open."""
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(START_BUILD_SQL, {
                    "water_source_id": water_source_id,
                    "phone": phone,
                    "language": language,
                })
                row = cur.fetchone()
            conn.commit()
        return row is not None
    except Exception:  # noqa: BLE001
        log.exception("Failed to start build for water_source_id=%s", water_source_id)
        return False


def update_build(
    water_source_id: str,
    status: str,
    stage: str | None = None,
    stage_index: int | None = None,
    stage_total: int | None = None,
    progress: int | None = None,
    error: str | None = None,
) -> None:
    """Persist a stage update (called by the builder job)."""
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(UPDATE_BUILD_SQL, {
                "water_source_id": water_source_id,
                "status": status,
                "stage": stage,
                "stage_index": stage_index,
                "stage_total": stage_total,
                "progress": progress if progress is not None else 0,
                "error": error,
            })
        conn.commit()


def get_build(water_source_id: str) -> BuildRecord | None:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(GET_BUILD_SQL, {"water_source_id": water_source_id})
            row = cur.fetchone()
    return _from_row(row) if row else None


def get_build_for_phone(phone: str) -> BuildRecord | None:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(GET_BUILD_FOR_PHONE_SQL, {"phone": phone})
            row = cur.fetchone()
    return _from_row(row) if row else None


def claim_pending_builds() -> list[BuildRecord]:
    """Return (and lock) all pending builds, oldest first. Used by the builder
    job; `for update skip locked` prevents two concurrent jobs claiming the
    same row."""
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(CLAIM_PENDING_SQL)
            rows = cur.fetchall()
        conn.commit()
    return [_from_row(r) for r in rows]
