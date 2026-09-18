"""Geo utilities: distance and travel time between coordinates.

Two modes:
1. amap (Gaode)  -> real driving distance & time  (needs AMAP_KEY)
2. fallback      -> haversine distance + average speed (no key, works now)

Get an amap key: sign up at lbs.amap.com, create a "Web Service" key,
then:  export AMAP_KEY="your_key"
"""

from __future__ import annotations

import math
import os
from typing import Optional, Tuple

try:
    import requests  # type: ignore
except ImportError:
    requests = None  # type: ignore

AMAP_GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
AMAP_DISTANCE_URL = "https://restapi.amap.com/v3/distance"

# City roads are longer than a straight line; rough correction factor.
ROAD_FACTOR = 1.35


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return the great-circle distance in kilometers between two points."""
    earth_radius_km = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lng2 - lng1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return earth_radius_km * 2 * math.asin(math.sqrt(a))


class GeoService:
    """Provides distance (km) and travel time (minutes) between two points."""

    def __init__(self, amap_key: Optional[str] = None, avg_speed_kmh: float = 22.0):
        self.amap_key = amap_key or os.environ.get("AMAP_KEY")
        self.avg_speed_kmh = avg_speed_kmh
        self.use_amap = bool(self.amap_key) and requests is not None

    def distance_time(
        self, lat1: float, lng1: float, lat2: float, lng2: float
    ) -> Tuple[float, float]:
        """Return (km, minutes) between two points, amap if available else fallback."""
        if self.use_amap:
            result = self._amap_distance(lat1, lng1, lat2, lng2)
            if result is not None:
                return result
        return self._fallback_distance(lat1, lng1, lat2, lng2)

    def _fallback_distance(
        self, lat1: float, lng1: float, lat2: float, lng2: float
    ) -> Tuple[float, float]:
        road_km = haversine_km(lat1, lng1, lat2, lng2) * ROAD_FACTOR
        minutes = (road_km / max(self.avg_speed_kmh, 1)) * 60.0
        return round(road_km, 3), round(minutes, 1)

    def _amap_distance(
        self, lat1: float, lng1: float, lat2: float, lng2: float
    ) -> Optional[Tuple[float, float]]:
        # amap expects coordinates as "lng,lat"
        params = {
            "key": self.amap_key,
            "origins": f"{lng1},{lat1}",
            "destination": f"{lng2},{lat2}",
            "type": "1",  # 1 = driving
        }
        try:
            response = requests.get(AMAP_DISTANCE_URL, params=params, timeout=8)
            data = response.json()
            if data.get("status") == "1" and data.get("results"):
                item = data["results"][0]
                km = float(item["distance"]) / 1000.0
                minutes = float(item["duration"]) / 60.0
                return round(km, 3), round(minutes, 1)
        except Exception:
            return None
        return None

    def geocode(self, address: str, city: str = "") -> Optional[Tuple[float, float]]:
        """Convert a text address into (lat, lng). Needs an amap key."""
        if not self.use_amap:
            return None
        params = {"key": self.amap_key, "address": address}
        if city:
            params["city"] = city
        try:
            response = requests.get(AMAP_GEOCODE_URL, params=params, timeout=8)
            data = response.json()
            if data.get("status") == "1" and data.get("geocodes"):
                lng_str, lat_str = data["geocodes"][0]["location"].split(",")
                return float(lat_str), float(lng_str)
        except Exception:
            return None
        return None


if __name__ == "__main__":
    # People's Square -> Lujiazui (Shanghai)
    print(f"Distance: {haversine_km(31.2290, 121.4750, 31.2397, 121.4998):.2f} km")