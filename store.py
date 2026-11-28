"""Order persistence in SQLite — a real database in a single file."""

from __future__ import annotations

import json
import os
import sqlite3

import db
from typing import List, Optional

from models import Order

HERE = os.path.dirname(os.path.abspath(__file__))
COLUMNS = ["id", "customer_name", "lat", "lng", "phone", "note", "service_minutes",
           "promised_by", "status", "created_at", "delivered_at", "driver_id", "rating", "proof",
           "price", "pay_method", "paid_at", "items", "trail"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id              TEXT PRIMARY KEY,
    customer_name   TEXT NOT NULL,
    lat             REAL NOT NULL,
    lng             REAL NOT NULL,
    phone           TEXT DEFAULT '',
    note            TEXT DEFAULT '',
    service_minutes REAL DEFAULT 5,
    promised_by     REAL,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      REAL,
    delivered_at    REAL,
    driver_id       TEXT,
    rating          INTEGER,
    proof           TEXT,
    price           REAL,
    pay_method      TEXT DEFAULT '',
    paid_at         REAL,
    items           TEXT DEFAULT '',
    trail           TEXT DEFAULT ''
)
"""


class OrderStore:
    def __init__(self, path: str = os.path.join(HERE, "orders.db")):
        self.path = path
        self.orders: List[Order] = []
        self._saved: dict = {}                  # id -> row as last written; lets save() write only changes
        con = db.connect(self.path)
        try:
            con.execute(SCHEMA)
            cols = {r[1] for r in con.execute("PRAGMA table_info(orders)")}
            if "driver_id" not in cols:         # migration: databases made before driver accounts
                con.execute("ALTER TABLE orders ADD COLUMN driver_id TEXT")
            if "rating" not in cols:            # migration: databases made before ratings
                con.execute("ALTER TABLE orders ADD COLUMN rating INTEGER")
            if "proof" not in cols:             # migration: databases made before proof photos
                con.execute("ALTER TABLE orders ADD COLUMN proof TEXT")
            if "price" not in cols:             # migration: databases made before payments
                con.execute("ALTER TABLE orders ADD COLUMN price REAL")
                con.execute("ALTER TABLE orders ADD COLUMN pay_method TEXT DEFAULT ''")
                con.execute("ALTER TABLE orders ADD COLUMN paid_at REAL")
            if "items" not in cols:             # migration: databases made before customer ordering
                con.execute("ALTER TABLE orders ADD COLUMN items TEXT DEFAULT ''")
            if "trail" not in cols:             # migration: databases made before saved delivery trails
                con.execute("ALTER TABLE orders ADD COLUMN trail TEXT DEFAULT ''")
            con.commit()
        finally:
            con.close()
        self._import_old_json()
        self.load()

    # ---------- helpers ----------
    def _run(self, sql: str, rows=None):
        """Run SQL inside ONE transaction: everything is saved, or nothing is."""
        con = db.connect(self.path)
        try:
            with con:
                if rows is None:
                    return con.execute(sql).fetchall()
                con.executemany(sql, rows)
        finally:
            con.close()

    @staticmethod
    def _row(order: Order) -> tuple:
        data = order.to_dict()
        return tuple(data.get(c) for c in COLUMNS)

    def _import_old_json(self) -> None:
        """One time only: copy orders.json into the database, then keep the JSON as a backup."""
        old = os.path.join(os.path.dirname(self.path), "orders.json")
        if not self.path.endswith(".db") or not os.path.exists(old):
            return
        if self._run("SELECT COUNT(*) FROM orders")[0][0] > 0:
            return
        with open(old, encoding="utf-8") as f:
            orders = [Order.from_dict(d) for d in json.load(f)]
        marks = ", ".join("?" for _ in COLUMNS)
        self._run(f"INSERT INTO orders ({', '.join(COLUMNS)}) VALUES ({marks})",
                  [self._row(o) for o in orders])
        os.rename(old, old + ".migrated")
        print(f"🗄️ Moved {len(orders)} orders from orders.json into SQLite", flush=True)

    # ---------- the same API as before ----------
    def load(self) -> None:
        """Read every order from the database."""
        rows = self._run(f"SELECT {', '.join(COLUMNS)} FROM orders ORDER BY created_at")
        self.orders = [Order.from_dict(dict(zip(COLUMNS, row))) for row in rows]
        self._saved = {o.id: self._row(o) for o in self.orders}

    def save(self) -> int:
        """Write only what changed since the last save: new, edited and removed orders.
        One atomic transaction, never half-written. Returns how many rows it touched."""
        current = {o.id: self._row(o) for o in self.orders}
        gone = [oid for oid in self._saved if oid not in current]
        changed = [row for oid, row in current.items() if self._saved.get(oid) != row]
        if not gone and not changed:
            return 0                                    # nothing to do: no database trip at all
        marks = ", ".join("?" for _ in COLUMNS)
        con = db.connect(self.path)
        try:
            with con:
                # Edited orders are replaced (delete + insert works the same on SQLite and Postgres)
                ids = gone + [row[0] for row in changed]
                con.executemany("DELETE FROM orders WHERE id = ?", [(i,) for i in ids])
                if changed:
                    con.executemany(f"INSERT INTO orders ({', '.join(COLUMNS)}) VALUES ({marks})", changed)
        finally:
            con.close()
        self._saved = current
        return len(gone) + len(changed)

    def add(self, order: Order) -> Order:
        marks = ", ".join("?" for _ in COLUMNS)
        row = self._row(order)
        self._run(f"INSERT INTO orders ({', '.join(COLUMNS)}) VALUES ({marks})", [row])
        self.orders.append(order)
        self._saved[order.id] = row
        return order

    def all(self) -> List[Order]:
        return list(self.orders)

    def get(self, order_id: str) -> Optional[Order]:
        """Find one order by its id (or None)."""
        return next((o for o in self.orders if o.id == order_id), None)