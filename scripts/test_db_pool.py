"""The Postgres pool: one warm connection per process instead of a handshake per query.

The measured problem (2026-10): `get_pg_connection()` opened a fresh TLS + SCRAM
session to Supabase for EVERY query and closed it straight after. One WhatsApp
answer makes several such queries; those handshakes were roughly half the waiting.

What this test pins, without a database:

  * importing `app.db` opens nothing (scripts and tests import it with no
    DATABASE_URL, and import-time network I/O is a trap);
  * with no DATABASE_URL the old, familiar error is raised;
  * a borrowed connection is RETURNED to the pool, not closed, and an exception
    rolls it back (commit on success);
  * when the pool cannot be opened the code degrades to short-lived connections
    instead of failing the request;
  * that degraded state is retried after a backoff (a paused Supabase project
    must not leave the process on the slow path for its whole life).

    python scripts/test_db_pool.py
"""
from __future__ import annotations

import logging
import sys
import types

sys.path.insert(0, ".")

from app import db  # noqa: E402

# --- 1) import must not touch the network ----------------------------------
assert db._POOL is None, "importing app.db must not open a pool"
print("import: no pool, no network OK")

# --- 2) no DATABASE_URL: the familiar error, unchanged ---------------------
_real_settings = db.get_settings
db.get_settings = lambda: types.SimpleNamespace(database_url="")
try:
    try:
        with db.get_pg_connection():
            pass
    except RuntimeError as exc:
        assert "DATABASE_URL is not set" in str(exc), exc
    else:
        raise AssertionError("missing DATABASE_URL must raise")
finally:
    db.get_settings = _real_settings
print("missing url: same RuntimeError as before OK")


# --- 3) pooled path: borrow, commit, RETURN (never close) ------------------
events: list[str] = []


class FakeConn:
    def __init__(self) -> None:
        self.committed = False


class FakePool:
    """Mimics psycopg_pool: connection() commits on clean exit, rolls back on error,
    and gives the connection back to the pool either way."""

    def __init__(self) -> None:
        self.conn = FakeConn()

    def connection(self):
        outer = self

        class _Ctx:
            def __enter__(self):
                events.append("borrow")
                return outer.conn

            def __exit__(self, exc_type, exc, tb):
                if exc_type is None:
                    outer.conn.committed = True
                    events.append("commit+return")
                else:
                    outer.conn.committed = False
                    events.append("rollback+return")
                return False

        return _Ctx()


pool = FakePool()
db.get_settings = lambda: types.SimpleNamespace(database_url="postgresql://fake/db")
db._POOL = pool
try:
    with db.get_pg_connection() as conn:
        assert conn is pool.conn
    assert events == ["borrow", "commit+return"], events
    assert pool.conn.committed is True

    events.clear()
    try:
        with db.get_pg_connection():
            raise ValueError("query blew up")
    except ValueError:
        pass
    assert events == ["borrow", "rollback+return"], events
finally:
    db._POOL = None
    db.get_settings = _real_settings
print("pool: connection returned and rolled back on error, never closed OK")


# --- 4) no pool (unreachable DB): degrade to a short-lived connection ------
opened: list[str] = []


class DirectConn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        opened.append("rollback" if exc_type else "commit")
        return False

    def close(self) -> None:
        opened.append("close")


def fake_connect(url, row_factory=None):
    assert "connect_timeout=10" in url, url
    opened.append("connect")
    return DirectConn()


_real_connect = db.psycopg.connect
db.psycopg.connect = fake_connect
db.get_settings = lambda: types.SimpleNamespace(database_url="postgresql://fake/db")
db._POOL_FAILED = True  # the pool already refused to open (no network in tests)
db._POOL_RETRY_AT = float("inf")  # ...and is inside its retry backoff
try:
    with db.get_pg_connection():
        pass
finally:
    db.psycopg.connect = _real_connect
    db.get_settings = _real_settings
    db._POOL_FAILED = False
    db._POOL_RETRY_AT = 0.0
assert opened == ["connect", "commit", "close"], opened
print("fallback: no pool -> connect/commit/close, the old behaviour OK")


# --- 5) a failed open is remembered, but retried after the backoff ----------
# Supabase pauses idle free projects: a process that booted during the pause must
# pick the pool up once the database is back, without paying the pool timeout on
# every message in between.
import psycopg_pool  # noqa: E402

attempts = {"n": 0}


class UnopenablePool:
    def __init__(self, **_kwargs) -> None:
        attempts["n"] += 1
        raise RuntimeError("database is asleep")


_real_pool_class = psycopg_pool.ConnectionPool
psycopg_pool.ConnectionPool = UnopenablePool
_real_level = logging.getLogger("app.db").level
logging.getLogger("app.db").setLevel(logging.CRITICAL)  # expected failure; no traceback spam
db.get_settings = lambda: types.SimpleNamespace(database_url="postgresql://fake/db")
db._POOL_FAILED = False
db._POOL_RETRY_AT = 0.0
try:
    assert db._pool() is None
    assert attempts["n"] == 1, attempts
    assert db._POOL_FAILED and db._POOL_RETRY_AT > 0, (db._POOL_FAILED, db._POOL_RETRY_AT)
    # Inside the backoff: no second attempt, no second timeout.
    assert db._pool() is None
    assert attempts["n"] == 1, attempts
    # Once the backoff has passed, a request tries again.
    db._POOL_RETRY_AT = 0.0
    assert db._pool() is None
    assert attempts["n"] == 2, attempts
finally:
    psycopg_pool.ConnectionPool = _real_pool_class
    logging.getLogger("app.db").setLevel(_real_level)
    db.get_settings = _real_settings
    db._POOL_FAILED = False
    db._POOL_RETRY_AT = 0.0
print("backoff: a failed pool open is retried, not on every message OK")

print("\nDB POOL OK")
