"""
Database access. Two clients, deliberately:

  - Supabase client (service_role key): used by scripts and the API for normal
    row reads/writes on the vector tables. Bypasses RLS by design (see
    migrations/001_init_schema.sql for why that's safe here).
  - Raw psycopg connection: used only where we need PostGIS SQL functions
    (ST_DWithin, ST_Buffer, ST_Contains, etc.) that the Supabase REST client
    can't express — e.g. nearest-water and species-ring containment queries.

Why a POOL (measured, 2026-10): every `get_pg_connection()` used to be a fresh
psycopg.connect() with a full TLS + SCRAM handshake to Supabase, paid again for
every query and closed immediately after. One WhatsApp answer makes several such
queries, and the handshakes alone were ~6 of the ~11 seconds a herder waited
(Render free instance -> Supabase). A ConnectionPool keeps a warm session and
turns each query into a round trip on an open socket.

The pool is created LAZILY, on first use, and never at import time: scripts and
tests import this module without a DATABASE_URL, and importing must stay free of
network I/O. If the pool cannot be created the code degrades to the old
short-lived-connection behaviour rather than failing the request.
"""
from __future__ import annotations

import atexit
import logging
import threading
from contextlib import contextmanager
from functools import lru_cache
from time import monotonic
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from supabase import create_client, Client

from app.config import get_settings

log = logging.getLogger(__name__)

# min_size=1 keeps one warm connection (one Supabase slot); max_size covers the
# dashboard's concurrent reads without letting one traffic spike exhaust Supabase's
# direct-connection limit.
POOL_MIN = 1
POOL_MAX = 6
POOL_WAIT_S = 10.0

_POOL = None
_POOL_LOCK = threading.Lock()
_POOL_FAILED = False
_POOL_RETRY_AT = 0.0
POOL_RETRY_S = 300.0  # after a failed open, try again at most every 5 minutes

MISSING_URL_MESSAGE = (
    "DATABASE_URL is not set. Copy .env.example to .env and fill in your "
    "Supabase Postgres connection string (Settings -> Database -> Connection string)."
)


@lru_cache
def get_supabase_client() -> Client:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set. Copy .env.example "
            "to .env and fill in your Supabase project credentials."
        )
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def _conninfo() -> str:
    """Connection string with a connect timeout, so a flaky network cannot hang
    the request path."""
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError(MISSING_URL_MESSAGE)
    url = settings.database_url
    sep = "&" if "?" in url else "?"
    if "connect_timeout=" in url:
        return url
    return f"{url}{sep}connect_timeout=10"


def _pool():
    """The shared pool, or None when it cannot be opened (caller falls back)."""
    global _POOL, _POOL_FAILED, _POOL_RETRY_AT
    if not get_settings().database_url:
        return None
    if _POOL is not None:
        return _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            return _POOL
        if _POOL_FAILED and monotonic() < _POOL_RETRY_AT:
            # A failed open is remembered for a few minutes: retrying it on every
            # message would add its timeout to every reply. It must not be forever
            # either -- Supabase pauses idle free projects, and a process that
            # booted during the pause should pick up the pool once it wakes.
            return None
        try:
            from psycopg_pool import ConnectionPool

            pool = ConnectionPool(
                conninfo=_conninfo(),
                kwargs={"row_factory": dict_row},
                min_size=POOL_MIN,
                max_size=POOL_MAX,
                timeout=POOL_WAIT_S,
                open=False,
                name="arda-pg",
            )
            pool.open(wait=True, timeout=POOL_WAIT_S)
        except Exception:  # noqa: BLE001
            _POOL_FAILED = True
            _POOL_RETRY_AT = monotonic() + POOL_RETRY_S
            log.warning("db: pool unavailable, using short-lived connections",
                        exc_info=True)
            return None
        _POOL_FAILED = False
        _POOL = pool
        # Close before interpreter finalisation: an un-closed pool's __del__ tries to
        # join its worker threads at shutdown and raises PythonFinalizationError,
        # which turns a clean test run into noise on stderr.
        atexit.register(pool.close)
        log.info("db: connection pool open (min=%d max=%d)", POOL_MIN, POOL_MAX)
        return _POOL


def close_pool() -> None:
    """Close the pool (app shutdown). Fail-open: never raises."""
    global _POOL
    pool, _POOL = _POOL, None
    if pool is None:
        return
    try:
        pool.close()
    except Exception:  # noqa: BLE001
        log.debug("db: pool close failed", exc_info=True)


def warm_pool() -> None:
    """Open the pool ahead of the first request (called from a startup thread).

    Fail-open by design: the app must serve /health even when the database is
    unreachable, so nothing here may raise.
    """
    try:
        _pool()
    except Exception:  # noqa: BLE001
        log.debug("db: pool warm-up skipped", exc_info=True)


@contextmanager
def get_pg_connection() -> Iterator[psycopg.Connection]:
    """A pooled PostGIS connection, used as `with get_pg_connection() as conn:`.

    Commit on a clean exit, rollback on an exception, and the connection goes back
    to the pool instead of being torn down -- the same semantics callers already
    relied on, minus the handshake. Call sites must use `with` (they all do): a
    borrowed connection has to be returned, and closing it by hand would throw the
    pooled session away.
    """
    url = _conninfo()  # raises the familiar error when DATABASE_URL is missing
    pool = _pool()
    if pool is None:
        conn = psycopg.connect(url, row_factory=dict_row)
        try:
            with conn:
                yield conn
        finally:
            conn.close()
        return
    with pool.connection() as conn:
        yield conn
