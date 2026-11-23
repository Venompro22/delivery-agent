"""
Shop settings the owner changes from the app: delivery fee, free delivery
threshold and minimum order. Stored as key -> value rows so new settings can
be added later without a migration.
"""
from __future__ import annotations

import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS shop_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

DEFAULTS = {"delivery_fee": 0.0, "free_over": 0.0, "min_order": 0.0}
MAX_VALUE = 10000


def clean(body: dict) -> tuple[dict, str | None]:
    """Validate the settings that were sent. Empty means 0. Returns (values, error)."""
    out = {}
    for key in DEFAULTS:
        if key not in body:
            continue
        raw = str(body[key]).strip() or "0"
        try:
            value = round(float(raw), 2)
        except ValueError:
            return {}, f"{key} must be a number"
        if not (0 <= value <= MAX_VALUE):          # also rejects nan and inf
            return {}, f"{key} is out of range"
        out[key] = value
    return out, None


def delivery_fee_for(subtotal: float, settings: dict) -> float:
    """Delivery fee for a cart: free from the threshold up, when one is set."""
    if settings["free_over"] > 0 and subtotal >= settings["free_over"]:
        return 0.0
    return settings["delivery_fee"]


class SettingsStore:
    def __init__(self, path: str):
        self.path = path
        con = db.connect(self.path)
        try:
            con.execute(SCHEMA)
            con.commit()
        finally:
            con.close()

    def all(self) -> dict:
        """Every setting, with defaults for the ones never saved."""
        values = dict(DEFAULTS)
        con = db.connect(self.path)
        try:
            rows = con.execute("SELECT key, value FROM shop_settings").fetchall()
        finally:
            con.close()
        for key, value in rows:
            if key in values:
                try:
                    values[key] = float(value)
                except ValueError:
                    pass                               # keep the default
        return values

    def update(self, **values) -> dict:
        con = db.connect(self.path)
        try:
            for key, value in values.items():
                con.execute("DELETE FROM shop_settings WHERE key = ?", (key,))
                con.execute("INSERT INTO shop_settings (key, value) VALUES (?, ?)", (key, str(value)))
            con.commit()
        finally:
            con.close()
        return self.all()
