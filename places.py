"""
Learned delivery spots: where each customer really receives their orders.

When a driver taps Delivered with a fresh GPS fix close to the pin, that spot is
remembered for the customer's phone (a running average, so one bad fix does not
spoil it). Next time a pin for the same phone lands nearby, it moves onto it.
"""
from __future__ import annotations

import time

import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS customer_places (
    phone      TEXT PRIMARY KEY,
    lat        REAL NOT NULL,
    lng        REAL NOT NULL,
    samples    INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL
)
"""
MAX_SAMPLES = 10       # average over the last ~10 deliveries, so the spot can still move


class PlaceStore:
    def __init__(self, path: str):
        self.path = path
        con = db.connect(self.path)
        try:
            con.execute(SCHEMA)
            con.commit()
        finally:
            con.close()

    def get(self, phone: str):
        """(lat, lng, samples) for this phone, or None."""
        con = db.connect(self.path)
        try:
            row = con.execute("SELECT lat, lng, samples FROM customer_places WHERE phone = ?",
                              (phone,)).fetchone()
        finally:
            con.close()
        return (row[0], row[1], row[2]) if row else None

    def learn(self, phone: str, lat: float, lng: float) -> tuple:
        """Blend a new delivery spot into the average for this phone."""
        old = self.get(phone)
        if old:
            n = min(old[2], MAX_SAMPLES - 1)
            lat = (old[0] * n + lat) / (n + 1)
            lng = (old[1] * n + lng) / (n + 1)
            samples = min(old[2] + 1, MAX_SAMPLES)
        else:
            samples = 1
        con = db.connect(self.path)
        try:
            con.execute("DELETE FROM customer_places WHERE phone = ?", (phone,))
            con.execute("INSERT INTO customer_places (phone, lat, lng, samples, updated_at) "
                        "VALUES (?, ?, ?, ?, ?)", (phone, lat, lng, samples, time.time()))
            con.commit()
        finally:
            con.close()
        return lat, lng, samples
