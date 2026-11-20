"""
Keep a few small state files in the database so they survive Render deploys
(Render's free disk is wiped on every deploy):

- .owner_2fa       owner's 2FA secret
- .panic_mode      kill switch state
- .session_version "log everyone out" counter

On SQLite (local Mac, tests) this module does nothing: the files on disk stay
the only source of truth, exactly as before.
"""
from __future__ import annotations

import os
import time

import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_files (
    name       TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    updated_at REAL NOT NULL
)
"""


def _connect():
    con = db.connect("")          # path is ignored on Postgres
    con.execute(SCHEMA)
    return con


def restore(paths: list[str]) -> None:
    """At startup: write every saved file back to disk."""
    if not db.is_postgres():
        return
    con = _connect()
    try:
        for path in paths:
            row = con.execute("SELECT content FROM app_files WHERE name = ?",
                              (os.path.basename(path),)).fetchone()
            if row is not None:
                with open(path, "w") as f:
                    f.write(row[0])
                os.chmod(path, 0o600)       # secrets: owner-only
        con.commit()
    finally:
        con.close()


def persist(path: str) -> None:
    """After a write or delete: copy the file's current state to the database."""
    if not db.is_postgres():
        return
    name = os.path.basename(path)
    con = _connect()
    try:
        con.execute("DELETE FROM app_files WHERE name = ?", (name,))
        if os.path.exists(path):           # a deleted file stays deleted
            with open(path) as f:
                content = f.read()
            con.execute("INSERT INTO app_files (name, content, updated_at) VALUES (?, ?, ?)",
                        (name, content, time.time()))
        con.commit()
    finally:
        con.close()
