"""Order persistence in a JSON file."""

from __future__ import annotations

import json
import os
from typing import List, Optional

from models import Order


class OrderStore:
    def __init__(self, path: str = "orders.json"):
        self.path = path
        self.orders: List[Order] = []
        self.load()

    def load(self) -> None:
        """Read orders from the JSON file (if it exists)."""
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self.orders = [Order.from_dict(d) for d in json.load(f)]
        else:
            self.orders = []

    def save(self) -> None:
        """Write all orders to the JSON file."""
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump([o.to_dict() for o in self.orders],
                      f, ensure_ascii=False, indent=2)

    def add(self, order: Order) -> Order:
        self.orders.append(order)
        self.save()
        return order

    def all(self) -> List[Order]:
        return list(self.orders)

    def get(self, order_id: str) -> Optional[Order]:
        """Find one order by its id (or None)."""
        return next((o for o in self.orders if o.id == order_id), None)