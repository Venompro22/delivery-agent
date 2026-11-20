"""
Delivery proof photos stored in the database.

Render's free disk is wiped on every deploy, so photos saved only as files
would disappear. The database (Neon Postgres) keeps them for good.
"""
from __future__ import annotations

import time

import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS proof_photos (
    name       TEXT PRIMARY KEY,
    mime       TEXT NOT NULL,
    data       BLOB NOT NULL,
    created_at REAL NOT NULL
)
"""


class PhotoStore:
    def __init__(self, path: str):
        self.path = path
        con = db.connect(self.path)
        try:
            con.execute(SCHEMA)
            con.commit()
        finally:
            con.close()

    def put(self, name: str, data: bytes, mime: str) -> None:
        """Save (or replace) one photo."""
        con = db.connect(self.path)
        try:
            con.execute("DELETE FROM proof_photos WHERE name = ?", (name,))
            con.execute(
                "INSERT INTO proof_photos (name, mime, data, created_at) VALUES (?, ?, ?, ?)",
                (name, mime, data, time.time()))
            con.commit()
        finally:
            con.close()

    def get(self, name: str):
        """Return (bytes, mime) or None when the photo is not in the database."""
        con = db.connect(self.path)
        try:
            row = con.execute(
                "SELECT data, mime FROM proof_photos WHERE name = ?", (name,)).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return bytes(row[0]), row[1]
