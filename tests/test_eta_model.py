"""Learned ETA: the model finds slow hours, needs enough data, and drives route ETAs."""
import random
from datetime import datetime

import optimizer
from eta_model import EtaModel, legs_from_orders, MIN_LEGS
from models import Order, OrderStatus, Driver


def at(day, hour, minute=10):
    return datetime(2026, 11, day, hour, minute).timestamp()


def synthetic_legs(n=200, seed=1):
    """Evening rush (17-20h) is twice as slow per km. Baseline ignores the hour."""
    rnd, legs = random.Random(seed), []
    for i in range(n):
        hour = rnd.choice([9, 10, 14, 18, 19])
        km = rnd.uniform(0.3, 3.0)
        pace = 8.0 if 17 <= hour < 20 else 4.0
        minutes = 3 + km * pace + rnd.gauss(0, 0.5)
        legs.append((km, minutes, at(1 + i % 28, hour), 3 + km * 5.0))
    return sorted(legs, key=lambda leg: leg[2])


def test_learns_that_rush_hour_is_slower():
    model = EtaModel.fit(synthetic_legs())
    assert model.predict(2, at(5, 18)) > model.predict(2, at(5, 10)) + 5
    assert abs(model.base - 3) < 1


def test_needs_enough_legs():
    assert EtaModel.fit(synthetic_legs()[:MIN_LEGS - 1]) is None
    assert EtaModel.train(synthetic_legs()[:MIN_LEGS - 1]) is None


def test_used_only_when_better_than_the_old_formula():
    model = EtaModel.train(synthetic_legs())
    assert model.active and model.mae < model.baseline_mae
    info = model.info()
    assert info["slowest"]["hours"] == "18-21h" and len(info["pace"]) == 8


def test_legs_come_from_consecutive_deliveries_of_one_driver_one_day():
    def done(t, lat, driver="d1"):
        return Order(customer_name="x", lat=lat, lng=120.0, driver_id=driver,
                     status=OrderStatus.DELIVERED, created_at=t - 600, delivered_at=t)
    orders = [done(at(3, 9, 0), 30.00), done(at(3, 9, 12), 30.01), done(at(3, 9, 20), 30.02),
              done(at(3, 13, 0), 30.03),                      # 3h40 later: a break, not a leg
              done(at(3, 9, 15), 30.05, driver="d2"),         # alone that day: no leg
              done(at(4, 9, 5), 30.00)]                       # next day: no leg
    legs = legs_from_orders(orders, baseline=lambda a, b: 7.0)
    assert [round(leg[1]) for leg in legs] == [12, 8]
    assert all(leg[3] == 7.0 for leg in legs)


def test_optimizer_uses_the_learned_model():
    class TenMinutes:
        def predict(self, km, at_epoch):
            return 10.0

    class FlatGeo:
        def distance_time(self, a, b, c, d):
            return 1.0, 2.0

    orders = [Order(customer_name=n, lat=30.2 + i / 100, lng=120.0) for i, n in enumerate("ab")]
    old = optimizer.ETA_PROVIDER
    optimizer.ETA_PROVIDER = lambda: TenMinutes()
    try:
        route = optimizer.optimize_route(Driver(name="d", lat=30.2, lng=120.0), orders, FlatGeo(),
                                         start_time=1000)
    finally:
        optimizer.ETA_PROVIDER = old
    assert [o.eta_epoch for o in route] == [1600, 2200]      # 10 minutes per leg
