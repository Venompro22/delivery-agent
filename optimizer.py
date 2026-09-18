"""Route optimization: order deliveries by distance + delivery-time urgency."""

from __future__ import annotations

import time
from typing import List, Optional

from models import Order, Driver
from geo import GeoService


def _urgency_bonus(order: Order, now: float) -> float:
    """Priority bonus (in 'km equivalent') for time-sensitive orders.

    A larger bonus lowers the order's score, so it is picked sooner.
    Orders without a promise get no bonus.
    """
    if order.promised_by is None:
        return 0.0
    minutes_left = (order.promised_by - now) / 60.0
    if minutes_left <= 0:
        return 100.0   # already late: strongest priority
    if minutes_left < 30:
        return 30.0    # urgent
    if minutes_left < 60:
        return 10.0    # soon
    return 0.0         # plenty of time


def optimize_route(
    driver: Driver,
    orders: List[Order],
    geo: GeoService,
    start_time: Optional[float] = None,
) -> List[Order]:
    """Order open deliveries by a score of distance minus urgency, with ETAs.

    score = distance_km - urgency_bonus  (lower is better)
    So a nearby stop wins, but an urgent promise can pull a farther one forward.

    Args:
        driver: The driver with a starting location.
        orders: Orders to consider (only open ones are routed).
        geo: Service used to measure distance and travel time.
        start_time: Epoch seconds the route begins; defaults to now.

    Returns:
        The open orders ordered as a delivery route.
    """
    start_time = start_time or time.time()
    remaining = [o for o in orders if o.is_open]
    route: List[Order] = []

    current_lat, current_lng = driver.lat, driver.lng
    elapsed_minutes = 0.0
    sequence = 0

    while remaining:
        nearest = None
        nearest_km = float("inf")
        nearest_minutes = 0.0
        best_score = float("inf")

        for order in remaining:
            km, minutes = geo.distance_time(
                current_lat, current_lng, order.lat, order.lng
            )
            # Lower score is better: closer OR more urgent wins.
            score = km - _urgency_bonus(order, time.time())
            if score < best_score:
                nearest, nearest_km, nearest_minutes = order, km, minutes
                best_score = score

        assert nearest is not None

        sequence += 1
        elapsed_minutes += nearest_minutes
        nearest.sequence = sequence
        nearest.leg_distance_km = round(nearest_km, 2)
        nearest.eta_epoch = start_time + elapsed_minutes * 60.0
        elapsed_minutes += nearest.service_minutes

        route.append(nearest)
        remaining.remove(nearest)
        current_lat, current_lng = nearest.lat, nearest.lng

    return route


def route_summary(route: List[Order]) -> dict:
    """Aggregate stats for a computed route."""
    total_km = sum((o.leg_distance_km or 0.0) for o in route)
    late = [
        o.id for o in route
        if o.promised_by and o.eta_epoch and o.eta_epoch > o.promised_by
    ]
    finish_epoch = route[-1].eta_epoch if route else None
    return {
        "stops": len(route),
        "total_km": round(total_km, 2),
        "finish_epoch": finish_epoch,
        "late_orders": late,
    }