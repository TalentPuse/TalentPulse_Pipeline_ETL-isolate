"""Shared Postgres connection context manager."""
from contextlib import contextmanager

import psycopg2


@contextmanager
def pg_connection(dsn: str):
    """Yield a psycopg2 connection with auto-commit/rollback."""
    conn = psycopg2.connect(dsn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
