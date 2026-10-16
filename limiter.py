"""🛡️ Login brute-force protection, stored in SQLite: survives restarts and is shared by all workers."""
from __future__ import annotations

import os
import sqlite3
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WINDOW = 600                       # seconds: failures older than 10 minutes are forgotten
LIMITS = {"ip": 10, "phone": 5}    # max failures inside the window

SCHEMA = """
CREATE TABLE IF NOT EXISTS login_failures (
    key TEXT NOT NULL,
    at  REAL NOT NULL
)
"""
INDEX = "CREATE INDEX IF NOT EXISTS login_failures_key ON login_failures (key, at)"


class LoginLimiter:
    def __init__(self, path: str = os.path.join(HERE, "orders.db")):
        self.path = path
        con = sqlite3.connect(self.path)
        try:
            con.execute(SCHEMA)
            con.execute(INDEX)
            con.commit()
        finally:
            con.close()

    @staticmethod
    def _keys(ip: str, phone: str = "") -> list:
        keys = [("ip", "ip:" + ip)]
        if phone:
            keys.append(("phone", "phone:" + phone))
        return keys

    def blocked(self, ip: str, phone: str = "") -> bool:
        """True if this IP or this phone failed too many times in the last 10 minutes."""
        since = time.time() - WINDOW
        con = sqlite3.connect(self.path)
        try:
            for kind, key in self._keys(ip, phone):
                (count,) = con.execute(
                    "SELECT COUNT(*) FROM login_failures WHERE key = ? AND at > ?",
                    (key, since)).fetchone()
                if count >= LIMITS[kind]:
                    return True
        finally:
            con.close()
        return False

    def fail(self, ip: str, phone: str = "") -> None:
        """Remember one wrong password (and forget the old ones)."""
        now = time.time()
        con = sqlite3.connect(self.path)
        try:
            con.executemany("INSERT INTO login_failures (key, at) VALUES (?, ?)",
                            [(key, now) for _, key in self._keys(ip, phone)])
            con.execute("DELETE FROM login_failures WHERE at < ?", (now - WINDOW,))
            con.commit()
        finally:
            con.close()

    def success(self, phone: str) -> None:
        """Right password: this phone starts from zero again."""
        if not phone:
            return
        con = sqlite3.connect(self.path)
        try:
            con.execute("DELETE FROM login_failures WHERE key = ?", ("phone:" + phone,))
            con.commit()
        finally:
            con.close()
