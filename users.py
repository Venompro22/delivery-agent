"""👤 Driver accounts: sign up → pending → approved by the owner (or blocked)."""
from __future__ import annotations

import os
import re
import secrets
import sqlite3
import time
from typing import List, Optional

from werkzeug.security import generate_password_hash, check_password_hash

HERE = os.path.dirname(os.path.abspath(__file__))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    phone         TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'driver',
    status        TEXT NOT NULL DEFAULT 'pending',
    created_at    REAL NOT NULL,
    approved_at   REAL
)
"""

PHONE_RE = re.compile(r"1[3-9]\d{9}")   # mainland China mobile: 11 digits, starts with 13-19
STATUSES = ("pending", "approved", "blocked")
_DUMMY_HASH = generate_password_hash("timing-safe-dummy")


class UserStore:
    def __init__(self, path: str = os.path.join(HERE, "orders.db")):
        self.path = path
        con = self._connect()
        try:
            con.execute(SCHEMA)
            con.commit()
        finally:
            con.close()

    def _connect(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row      # rows behave like dicts: row["phone"]
        return con

    def _one(self, sql: str, params: tuple) -> Optional[dict]:
        con = self._connect()
        try:
            row = con.execute(sql, params).fetchone()
        finally:
            con.close()
        return dict(row) if row else None

    def create(self, phone: str, name: str, password: str) -> dict:
        """New driver account, waiting for approval. Raises ValueError if the input is bad."""
        phone, name = phone.strip(), name.strip()
        if not PHONE_RE.fullmatch(phone):
            raise ValueError("phone must be an 11-digit China mobile number")
        if not 1 <= len(name) <= 40:
            raise ValueError("name must be 1-40 characters")
        if len(password) < 8:
            raise ValueError("password must be at least 8 characters")
        user_id = secrets.token_hex(8)
        con = self._connect()
        try:
            con.execute(
                "INSERT INTO users (id, phone, name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, phone, name, generate_password_hash(password), time.time()))
            con.commit()
        except sqlite3.IntegrityError:
            raise ValueError("this phone number is already registered")
        finally:
            con.close()
        return self.get(user_id)

    def get(self, user_id: str) -> Optional[dict]:
        return self._one("SELECT * FROM users WHERE id = ?", (user_id,))

    def by_phone(self, phone: str) -> Optional[dict]:
        return self._one("SELECT * FROM users WHERE phone = ?", (phone.strip(),))

    def check_login(self, phone: str, password: str) -> Optional[dict]:
        """The user if phone + password are right, else None (same speed either way)."""
        user = self.by_phone(phone)
        if not user:
            check_password_hash(_DUMMY_HASH, password)   # don't reveal which phones exist
            return None
        return user if check_password_hash(user["password_hash"], password) else None

    def all(self) -> List[dict]:
        """Every account, pending ones first (they need your attention)."""
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT * FROM users ORDER BY status != 'pending', created_at DESC").fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]

    def set_status(self, user_id: str, status: str) -> bool:
        """Approve or block an account. Returns False if the user doesn't exist."""
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        approved_at = time.time() if status == "approved" else None
        con = self._connect()
        try:
            cur = con.execute("UPDATE users SET status = ?, approved_at = ? WHERE id = ?",
                              (status, approved_at, user_id))
            con.commit()
        finally:
            con.close()
        return cur.rowcount == 1


def public(user: dict) -> dict:
    """What's safe to send to the browser: never the password hash."""
    return {k: v for k, v in user.items() if k != "password_hash"}