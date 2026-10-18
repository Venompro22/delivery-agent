"""📜 Security log: who did what, when, and from where. Kept 90 days, then deleted."""
from __future__ import annotations

import os
import sqlite3
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KEEP_DAYS = 90

SCHEMA = """
CREATE TABLE IF NOT EXISTS security_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at     REAL NOT NULL,
    event  TEXT NOT NULL,
    ip     TEXT NOT NULL DEFAULT '',
    who    TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''
)
"""


class AuditLog:
    def __init__(self, path: str = os.path.join(HERE, "orders.db")):
        self.path = path
        con = sqlite3.connect(self.path)
        try:
            con.execute(SCHEMA)
            con.commit()
        finally:
            con.close()

    def add(self, event: str, ip: str = "", who: str = "", detail: str = "") -> None:
        """Write one event (and delete the ones older than 90 days)."""
        now = time.time()
        con = sqlite3.connect(self.path)
        try:
            con.execute(
                "INSERT INTO security_log (at, event, ip, who, detail) VALUES (?, ?, ?, ?, ?)",
                (now, event, ip[:45], who[:40], detail[:100]))      # strangers type these: cap the size
            con.execute("DELETE FROM security_log WHERE at < ?", (now - KEEP_DAYS * 86400,))
            con.commit()
        finally:
            con.close()

    def recent(self, limit: int = 50) -> list:
        """The newest events first."""
        con = sqlite3.connect(self.path)
        try:
            rows = con.execute(
                "SELECT at, event, ip, who, detail FROM security_log ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        finally:
            con.close()
        return [dict(zip(("at", "event", "ip", "who", "detail"), r)) for r in rows]
