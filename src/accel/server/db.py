"""Every database call goes through here. Postgres, on Supabase.

No `psycopg2` import anywhere else in the codebase. The previous version of
this file was SQLite and left a note saying "add the psycopg branch here" --
this is that branch, and the seam is why the swap touched one file.

Two things that were true of the SQLite version and are still true:

  * **Named parameters only.** Queries are written `:name` and translated to
    `%(name)s` on the way out. Positional `?` would have had to be rewritten
    by hand in every query.
  * **Rows come back as dicts.** Nothing downstream indexes a tuple.

Connections are per-thread and created lazily. A pool created at import time
multiplies per worker and exhausts Supabase's connection cap; per-thread and
lazy is the shape that survives being deployed.

The connection string must be the Supabase **session pooler** (port 5432 on
`*.pooler.supabase.com`). The direct-connection host is IPv6-only unless you
buy the IPv4 add-on, and Render cannot reach IPv6 -- it fails as a timeout,
which reads exactly like the database being down.
"""

from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Sequence

import psycopg2
import psycopg2.extras

_local = threading.local()

# :name, but not ::cast
_NAMED = re.compile(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)")


def _translate(sql: str) -> str:
    """`:name` -> `%(name)s`."""
    return _NAMED.sub(r"%(\1)s", sql)


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def connection():
    """This thread's connection, created on first use and reconnected if dead."""
    conn = getattr(_local, "conn", None)
    if conn is not None and conn.closed == 0:
        return conn
    conn = psycopg2.connect(database_url(), connect_timeout=20)
    conn.autocommit = True
    _local.conn = conn
    return conn


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


def _cursor():
    return connection().cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def query_all(sql: str, params: Mapping[str, Any] | None = None) -> list[dict]:
    with _cursor() as cur:
        cur.execute(_translate(sql), dict(params or {}))
        return [dict(r) for r in cur.fetchall()]


def query_one(sql: str, params: Mapping[str, Any] | None = None) -> dict | None:
    with _cursor() as cur:
        cur.execute(_translate(sql), dict(params or {}))
        row = cur.fetchone()
        return dict(row) if row is not None else None


def execute(sql: str, params: Mapping[str, Any] | None = None) -> int:
    """Run a statement.

    Returns the id when the statement carries `RETURNING id`, else the row
    count. The SQLite version returned `lastrowid`, which Postgres has no
    equivalent of -- callers that need the new id must ask for it.
    """
    with _cursor() as cur:
        cur.execute(_translate(sql), dict(params or {}))
        if cur.description:
            row = cur.fetchone()
            if row:
                return list(row.values())[0]
        return cur.rowcount


def execute_many(sql: str, rows: Sequence[Mapping[str, Any]]) -> int:
    if not rows:
        return 0
    with _cursor() as cur:
        cur.executemany(_translate(sql), [dict(r) for r in rows])
        return cur.rowcount


def script(ddl: str) -> None:
    """Run DDL. Schema lives below; this only executes it."""
    with _cursor() as cur:
        cur.execute(ddl)


@contextmanager
def transaction() -> Iterator[Any]:
    """All-or-nothing. Commits on clean exit, rolls back on any exception."""
    conn = connection()
    conn.autocommit = False
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.autocommit = True


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,          -- Supabase auth user id
    github_login    TEXT NOT NULL,
    github_token    TEXT,                      -- see auth.py: captured at sign-in
    avatar_url      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    user_id         TEXT REFERENCES users(id) ON DELETE CASCADE,
    url             TEXT NOT NULL,
    repo            TEXT NOT NULL,
    branch          TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'queued',
    phase           TEXT NOT NULL DEFAULT '',
    pr_url          TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS jobs_user_started
    ON jobs (user_id, started_at DESC);

-- The lessons table is created by agent/lessons.py, which owns its shape.
"""


def init() -> None:
    """Create the schema if it is not there. Safe to call on every boot."""
    script(SCHEMA)


def ping() -> str:
    row = query_one("SELECT version() AS v")
    return (row or {}).get("v", "")
