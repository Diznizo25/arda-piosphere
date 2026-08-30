"""
Database access. Two clients, deliberately:

  - Supabase client (service_role key): used by scripts and the API for normal
    row reads/writes on the vector tables. Bypasses RLS by design (see
    migrations/001_init_schema.sql for why that's safe here).
  - Raw psycopg connection: used only where we need PostGIS SQL functions
    (ST_DWithin, ST_Buffer, ST_Contains, etc.) that the Supabase REST client
    can't express — e.g. nearest-water and species-ring containment queries.
"""
from functools import lru_cache

import psycopg
from psycopg.rows import dict_row
from supabase import create_client, Client

from app.config import get_settings


@lru_cache
def get_supabase_client() -> Client:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set. Copy .env.example "
            "to .env and fill in your Supabase project credentials."
        )
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def get_pg_connection() -> psycopg.Connection:
    """Short-lived raw connection for PostGIS queries. Callers should use this
    as a context manager: `with get_pg_connection() as conn: ...`"""
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill in your "
            "Supabase Postgres connection string (Settings -> Database -> Connection string)."
        )
    # connect_timeout keeps a flaky network from hanging the request path.
    url = settings.database_url
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}connect_timeout=10"
    return psycopg.connect(url, row_factory=dict_row)
