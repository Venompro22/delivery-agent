"""Order persistence in SQLite — a real database in a single file."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import List, Optional

from models import Order

HERE = os.path.dirname(os.path.abspath(__file__))
COLUMNS = ["id", "customer_name", "lat", "lng", "phone", "note", "service_minutes",
           "promised_by", "status", "created_at", "delivered_at", "driver_id", "rating", "proof"]

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
    proof           TEXT
)
"""


class OrderStore:
    def __init__(self, path: str = os.path.join(HERE, "orders.db")):
        self.path = path
        self.orders: List[Order] = []
        con = sqlite3.connect(self.path)
        try:
            con.execute(SCHEMA)
            cols = {r[1] for r in con.execute("PRAGMA table_info(orders)")}
            if "driver_id" not in cols:         # migration: databases made before driver accounts
                con.execute("ALTER TABLE orders ADD COLUMN driver_id TEXT")
            if "rating" not in cols:            # migration: databases made before ratings
                con.execute("ALTER TABLE orders ADD COLUMN rating INTEGER")
            if "proof" not in cols:             # migration: databases made before proof photos
                con.execute("ALTER TABLE orders ADD COLUMN proof TEXT")
            con.commit()
        finally:
            con.close()
        self._import_old_json()
        self.load()

    # ---------- helpers ----------
    def _run(self, sql: str, rows=None):
        """Run SQL inside ONE transaction: everything is saved, or nothing is."""
        con = sqlite3.connect(self.path)
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

    def save(self) -> None:
        """Write all orders (one atomic transaction — never half-written)."""
        marks = ", ".join("?" for _ in COLUMNS)
        con = sqlite3.connect(self.path)
        try:
            with con:
                con.execute("DELETE FROM orders")
                con.executemany(f"INSERT INTO orders ({', '.join(COLUMNS)}) VALUES ({marks})",
                                [self._row(o) for o in self.orders])
        finally:
            con.close()

    def add(self, order: Order) -> Order:
        marks = ", ".join("?" for _ in COLUMNS)
        self._run(f"INSERT INTO orders ({', '.join(COLUMNS)}) VALUES ({marks})", [self._row(order)])
        self.orders.append(order)
        return order

    def all(self) -> List[Order]:
        return list(self.orders)

    def get(self, order_id: str) -> Optional[Order]:
        """Find one order by its id (or None)."""
        return next((o for o in self.orders if o.id == order_id), None)