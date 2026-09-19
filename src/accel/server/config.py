"""Seam 4: the worker never assumes where it runs.

Everything that needs a location asks this module. Nothing anywhere else in the
codebase writes an absolute path, reads an environment variable for a path, or
decides where the database lives.

Three rules this enforces:

  * No absolute paths. Every location derives from one root, which is
    ALLY_ROOT if set and the current working directory otherwise. On Render or
    in a container, set ALLY_ROOT and nothing else changes.
  * No assumption that the filesystem persists. Anything written under the
    root is disposable; anything that must survive a restart goes in the
    database, which is why `checkpoint_dir` is deliberately absent.
  * No connection pool per request. `db.py` owns connections and creates them
    per thread, never per handler. A pool built inside a serverless handler
    multiplies by instance count and exhausts the server's connection cap.
"""

from __future__ import annotations

import os
import pathlib


def root() -> pathlib.Path:
    """The one directory everything else hangs off."""
    return pathlib.Path(os.environ.get("ALLY_ROOT", ".")).resolve()


def runs_dir() -> pathlib.Path:
    """Where run artifacts live. Disposable: never put state here."""
    return root() / "runs"


def database_url() -> str:
    """Connection string. A path for SQLite, a URL for Postgres later.

    `db.py` picks a driver from the scheme, so moving to Postgres is this
    value changing and nothing else.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    return f"sqlite:///{root() / 'ally.db'}"


def is_sqlite() -> bool:
    return database_url().startswith("sqlite:")
