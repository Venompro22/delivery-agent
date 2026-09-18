"""Data models for the delivery agent."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class OrderStatus(str, Enum):
    """Lifecycle states of an order."""
    PENDING = "pending"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


@dataclass
class Order:
    """A single delivery order."""
    customer_name: str
    lat: float
    lng: float
    phone: str = ""
    # Minutes spent at the customer (hand-off, payment)
    service_minutes: float = 5.0
    # Deliver-by promise as epoch seconds; None means no promise
    promised_by: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    created_at: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    # Filled in by the optimizer
    sequence: Optional[int] = None          # 1 = first stop
    eta_epoch: Optional[float] = None        # predicted delivery time
    leg_distance_km: Optional[float] = None  # distance from previous stop

    @property
    def is_open(self) -> bool:
        """True if the order still needs delivering."""
        return self.status == OrderStatus.PENDING

    def to_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Order":
        data = dict(data)
        data["status"] = OrderStatus(data.get("status", "pending"))
        # Drop computed fields; they are recalculated each run
        for key in ("sequence", "eta_epoch", "leg_distance_km"):
            data.pop(key, None)
        return cls(**data)


@dataclass
class Driver:
    """A driver and their starting location (shop / restaurant / current spot)."""
    name: str
    lat: float
    lng: float
    avg_speed_kmh: float = 22.0