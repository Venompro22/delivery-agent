"""
Learned ETA: how long one delivery leg really takes, by time of day.

Training data comes from finished deliveries. For one driver on one day, the
time between two consecutive "Delivered" taps is one leg: driving there plus
handing the order over. We pair it with the straight-line distance between the
two stops and the hour it happened.

Model (one small linear regression solved with numpy):
    minutes = base + km * pace[bucket]
- base: fixed minutes per stop (parking, stairs, handing over)
- pace: minutes per km for each 3-hour bucket of the day (rush hour is slower)

Buckets with little data are pulled toward the overall pace (a ridge prior),
so one odd evening does not make a whole bucket wrong. The model is only used
when it beats the old fixed-speed formula on the most recent deliveries.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np

from geo import haversine_km

BUCKET_HOURS = 3
N_BUCKETS = 24 // BUCKET_HOURS
MIN_LEGS = 30          # fewer legs: not enough to learn anything
MIN_LEG_MIN = 0.5      # faster than this: two orders delivered together
MAX_LEG_MIN = 90       # slower than this: a break, not a leg
PRIOR = 5.0            # how strongly sparse buckets lean on the overall pace
TEST_SHARE = 0.2       # newest 20% of legs are kept aside to score the model


def bucket_of(epoch: float) -> int:
    return datetime.fromtimestamp(epoch).hour // BUCKET_HOURS


def bucket_label(bucket: int) -> str:
    start = bucket * BUCKET_HOURS
    return f"{start:02d}-{start + BUCKET_HOURS:02d}h"


def legs_from_orders(orders, baseline=None) -> list[tuple]:
    """
    (km, minutes, epoch, baseline_minutes) for consecutive deliveries of the
    same driver on the same day, oldest first. baseline(a, b) gives the old
    formula's minutes for the same leg, so both can be compared.
    """
    done = [o for o in orders
            if o.delivered_at and getattr(o.status, "value", o.status) == "delivered"]
    days = {}
    for o in done:
        day = datetime.fromtimestamp(o.delivered_at).date()
        days.setdefault((o.driver_id, day), []).append(o)
    legs = []
    for group in days.values():
        group.sort(key=lambda o: o.delivered_at)
        for a, b in zip(group, group[1:]):          # the first stop of a day has no known start
            minutes = (b.delivered_at - a.delivered_at) / 60
            if MIN_LEG_MIN <= minutes <= MAX_LEG_MIN:
                km = haversine_km(a.lat, a.lng, b.lat, b.lng)
                legs.append((km, minutes, b.delivered_at, baseline(a, b) if baseline else None))
    legs.sort(key=lambda leg: leg[2])
    return legs


class EtaModel:
    def __init__(self, base: float, pace: list[float], legs: int):
        self.base = base
        self.pace = pace
        self.legs = legs
        self.mae = None            # average error on recent legs (minutes)
        self.baseline_mae = None   # same, for the old fixed-speed formula
        self.active = False        # used for routes only when it beats the old formula

    def predict(self, km: float, at_epoch: float) -> float:
        """Minutes for one leg of `km` (straight line) starting around `at_epoch`."""
        return self.base + km * self.pace[bucket_of(at_epoch)]

    @classmethod
    def fit(cls, legs) -> "EtaModel | None":
        if len(legs) < MIN_LEGS:
            return None
        km = np.array([leg[0] for leg in legs], dtype=float)
        y = np.array([leg[1] for leg in legs], dtype=float)
        buckets = np.array([bucket_of(leg[2]) for leg in legs])

        # 1) Overall line: minutes = base + km * pace
        A = np.column_stack([np.ones_like(km), km])
        _, pace0 = np.linalg.lstsq(A, y, rcond=None)[0]

        # 2) One pace per bucket, shared base, each pace pulled toward pace0
        X = np.zeros((len(legs), 1 + N_BUCKETS))
        X[:, 0] = 1.0
        X[np.arange(len(legs)), 1 + buckets] = km
        prior_X = np.sqrt(PRIOR) * np.eye(1 + N_BUCKETS)[1:]
        prior_y = np.sqrt(PRIOR) * np.full(N_BUCKETS, pace0)
        coef = np.linalg.lstsq(np.vstack([X, prior_X]), np.concatenate([y, prior_y]), rcond=None)[0]

        base = float(max(coef[0], 0.0))
        pace = [float(max(p, 0.1)) for p in coef[1:]]      # never "faster than light"
        return cls(base, pace, len(legs))

    @classmethod
    def train(cls, legs) -> "EtaModel | None":
        """Score on the newest legs against the old formula, then fit on everything."""
        if len(legs) < MIN_LEGS:
            return None
        cut = int(len(legs) * (1 - TEST_SHARE))
        trial = cls.fit(legs[:cut]) if cut >= MIN_LEGS else None
        model = cls.fit(legs)
        if trial is not None:
            test = legs[cut:]
            model.mae = float(np.mean([abs(trial.predict(k, t) - m) for k, m, t, _ in test]))
            if all(leg[3] is not None for leg in test):
                model.baseline_mae = float(np.mean([abs(b - m) for _, m, _, b in test]))
                model.active = model.mae < model.baseline_mae
        return model

    def info(self) -> dict:
        """What the Stats page shows about the model."""
        slowest = max(range(N_BUCKETS), key=lambda b: self.pace[b])
        return {
            "active": self.active, "legs": self.legs,
            "mae": round(self.mae, 1) if self.mae is not None else None,
            "baseline_mae": round(self.baseline_mae, 1) if self.baseline_mae is not None else None,
            "base": round(self.base, 1),
            "pace": [{"hours": bucket_label(b), "min_per_km": round(p, 1)} for b, p in enumerate(self.pace)],
            "slowest": {"hours": bucket_label(slowest), "min_per_km": round(self.pace[slowest], 1)},
        }
