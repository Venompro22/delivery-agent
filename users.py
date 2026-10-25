"""👤 Driver accounts: sign up → pending → approved by the owner (or blocked)."""
from __future__ import annotations

import math
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
    approved_at   REAL,
    seen_at       REAL
)
"""

PHONE_RE = re.compile(r"1[3-9]\d{9}")   # mainland China mobile: 11 digits, starts with 13-19
STATUSES = ("pending", "approved", "blocked")
_DUMMY_HASH = generate_password_hash("timing-safe-dummy")

# The most used passwords (8+ characters) — attackers always try these first
COMMON_PASSWORDS = {
    "password", "password1", "password123", "iloveyou", "qwertyui", "qwerty123",
    "1qaz2wsx", "1q2w3e4r", "a1234567", "a12345678", "aa123456", "qq123456",
    "abc12345", "abc123456", "123456789a", "123qweasd", "11223344", "147258369",
    "123123123", "asdfghjk", "zxcvbnm123", "woaini1314", "5201314520", "1314520520",
}
SEQUENCES = ("01234567890", "09876543210", "abcdefghijklmnopqrstuvwxyz", "qwertyuiopasdfghjklzxcvbnm")


def weak_password(password: str, phone: str = "") -> bool:
    """True if the password is easy to guess."""
    p = password.lower()
    return (p in COMMON_PASSWORDS
            or len(set(p)) < 4                         # 88888888, 11112222, 12121212
            or any(p in seq for seq in SEQUENCES)      # 12345678, abcdefgh, qwertyui
            or bool(phone and phone in password))      # the phone number itself


TEMP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"   # no 0/O, 1/l/I


def check_password_rules(password: str, phone: str = "") -> None:
    """Raises ValueError if the password is not acceptable."""
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    if len(password) > 128:
        raise ValueError("password is too long")
    if weak_password(password, phone):
        raise ValueError("password is too easy to guess")


class UserStore:
    def __init__(self, path: str = os.path.join(HERE, "orders.db")):
        self.path = path
        con = self._connect()
        try:
            con.execute(SCHEMA)
            cols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
            if "pw_version" not in cols:      # +1 at every password change → old sessions die
                con.execute("ALTER TABLE users ADD COLUMN pw_version INTEGER NOT NULL DEFAULT 0")
            if "must_change" not in cols:     # 1 after an owner reset
                con.execute("ALTER TABLE users ADD COLUMN must_change INTEGER NOT NULL DEFAULT 0")
            for col in ("lat", "lng", "located_at", "seen_at"):     # 📍 last GPS position (no history kept)
                if col not in cols:
                    con.execute(f"ALTER TABLE users ADD COLUMN {col} REAL")
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
        check_password_rules(password, phone)
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

    def set_password(self, user_id: str, password: str, must_change: bool = False) -> bool:
        """New password. pw_version + 1 → every other device is logged out."""
        user = self.get(user_id)
        if not user:
            return False
        check_password_rules(password, user["phone"])
        con = self._connect()
        try:
            con.execute(
                "UPDATE users SET password_hash = ?, pw_version = pw_version + 1, must_change = ? "
                "WHERE id = ?",
                (generate_password_hash(password), int(must_change), user_id))
            con.commit()
        finally:
            con.close()
        return True

    def set_location(self, user_id: str, lat: float, lng: float) -> None:
        """📍 Only the latest position is kept — never a history of where a driver has been."""
        con = self._connect()
        try:
            con.execute("UPDATE users SET lat = ?, lng = ?, located_at = ? WHERE id = ?",
                        (lat, lng, time.time(), user_id))
            con.commit()
        finally:
            con.close()

    def get_seen_at(self, user_id: str) -> float:
        """When this driver last looked at their orders (0 if never)."""
        row = self._one("SELECT seen_at FROM users WHERE id = ?", (user_id,))
        return (row and row["seen_at"]) or 0.0

    def mark_seen(self, user_id: str) -> None:
        """Mark that the driver has now seen their orders (resets the badge)."""
        con = self._connect()
        try:
            con.execute("UPDATE users SET seen_at = ? WHERE id = ?",
                        (time.time(), user_id))
            con.commit()
        finally:
            con.close()

    def reset_password(self, user_id: str) -> Optional[str]:
        """Owner resets a forgotten password: a temporary one the driver must change."""
        temp = "".join(secrets.choice(TEMP_ALPHABET) for _ in range(12))
        return temp if self.set_password(user_id, temp, must_change=True) else None


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distance in km between two GPS points (Haversine formula)."""
    R = 6371.0  # Earth radius in km
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def find_nearest_driver(users_db, order_lat: float, order_lng: float):
    """Pick the closest approved driver that has a known GPS position.

    Returns the driver dict (nearest one), or None if no approved
    driver currently has a location. Distance is in km via haversine.
    """
    best = None
    best_dist = None
    for u in users_db.all():
        if u["role"] != "driver":
            continue
        if u["status"] != "approved":
            continue
        if u["lat"] is None or u["lng"] is None:
            continue
        dist = haversine(u["lat"], u["lng"], order_lat, order_lng)
        if best_dist is None or dist < best_dist:
            best = u
            best_dist = dist
    return best


def public(user: dict) -> dict:
    """What's safe to send to the browser: never the password hash."""
    return {k: v for k, v in user.items() if k != "password_hash"}