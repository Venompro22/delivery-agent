"""
Product catalog: what the shop sells and for how much.

The owner manages the list (name, price, unit). Products are never deleted,
only hidden, so old orders that mention them still make sense.
"""
from __future__ import annotations

import time
import uuid

import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    price      REAL NOT NULL,
    unit       TEXT NOT NULL DEFAULT '',
    active     INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
)
"""

COLUMNS = ("id", "name", "price", "unit", "active", "created_at")
MAX_NAME, MAX_UNIT, MAX_PRICE = 60, 20, 100000


def clean(name=None, price=None, unit=None) -> tuple[dict, str | None]:
    """Validate the fields that were given. Returns (fields, error message or None)."""
    out = {}
    if name is not None:
        name = str(name).strip()
        if not name:
            return {}, "name is required"
        if len(name) > MAX_NAME:
            return {}, f"name is too long (max {MAX_NAME} characters)"
        out["name"] = name
    if price is not None:
        try:
            value = round(float(str(price).strip()), 2)
        except ValueError:
            return {}, "price must be a number"
        if not (0 <= value <= MAX_PRICE):           # also rejects nan and inf
            return {}, "price is out of range"
        out["price"] = value
    if unit is not None:
        unit = str(unit).strip()
        if len(unit) > MAX_UNIT:
            return {}, f"unit is too long (max {MAX_UNIT} characters)"
        out["unit"] = unit
    return out, None


class ProductStore:
    def __init__(self, path: str):
        self.path = path
        con = db.connect(self.path)
        try:
            con.execute(SCHEMA)
            con.commit()
        finally:
            con.close()

    def _rows(self, sql: str, params=()) -> list[dict]:
        con = db.connect(self.path)
        try:
            rows = con.execute(sql, params).fetchall()
        finally:
            con.close()
        return [dict(zip(COLUMNS, r), active=bool(r[4])) for r in rows]

    def all(self, include_hidden: bool = False) -> list[dict]:
        """Products sorted by name; hidden ones only when asked."""
        where = "" if include_hidden else "WHERE active = 1"
        return self._rows(f"SELECT {', '.join(COLUMNS)} FROM products {where} ORDER BY name")

    def get(self, product_id: str) -> dict | None:
        rows = self._rows(f"SELECT {', '.join(COLUMNS)} FROM products WHERE id = ?", (product_id,))
        return rows[0] if rows else None

    def create(self, name: str, price: float, unit: str = "") -> dict:
        product = {"id": uuid.uuid4().hex[:8], "name": name, "price": price, "unit": unit,
                   "active": True, "created_at": time.time()}
        con = db.connect(self.path)
        try:
            con.execute("INSERT INTO products (id, name, price, unit, active, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (product["id"], name, price, unit, 1, product["created_at"]))
            con.commit()
        finally:
            con.close()
        return product

    def update(self, product_id: str, **fields) -> dict | None:
        """Change name / price / unit / active. Unknown keys are ignored."""
        allowed = {k: v for k, v in fields.items() if k in ("name", "price", "unit", "active")}
        if "active" in allowed:
            allowed["active"] = 1 if allowed["active"] else 0
        if allowed:
            sets = ", ".join(f"{k} = ?" for k in allowed)
            con = db.connect(self.path)
            try:
                con.execute(f"UPDATE products SET {sets} WHERE id = ?", (*allowed.values(), product_id))
                con.commit()
            finally:
                con.close()
        return self.get(product_id)
