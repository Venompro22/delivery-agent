"""Render a delivery route as an interactive HTML map (Leaflet)."""

from __future__ import annotations

import json
import webbrowser
from datetime import datetime
from typing import List

from models import Order, Driver


def _fmt(epoch):
    return datetime.fromtimestamp(epoch).strftime("%H:%M") if epoch else "--"


def render_map(driver: Driver, route: List[Order], path: str = "route.html") -> str:
    """Build an HTML map of the route and return the file path."""
    # Points: start with the driver, then each stop in order
    points = [{
        "lat": driver.lat, "lng": driver.lng,
        "label": f"🚚 {driver.name} (start)", "type": "driver",
    }]
    for o in route:
        points.append({
            "lat": o.lat, "lng": o.lng,
            "label": f"{o.sequence}. {o.customer_name} — ETA {_fmt(o.eta_epoch)}",
            "type": "stop",
        })

    html = _TEMPLATE.replace("__POINTS__", json.dumps(points, ensure_ascii=False))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Delivery Route</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    html, body, #map { height: 100%; margin: 0; }
  </style>
</head>
<body>
  <div id="map"></div>
  <script>
    const points = __POINTS__;
    const map = L.map('map').setView([points[0].lat, points[0].lng], 13);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap'
    }).addTo(map);

    const latlngs = [];
    points.forEach((p, i) => {
      latlngs.push([p.lat, p.lng]);
      const color = p.type === 'driver' ? 'green' : 'red';
      L.circleMarker([p.lat, p.lng], {
        radius: 9, color: color, fillColor: color, fillOpacity: 0.9
      }).addTo(map).bindPopup(p.label);
    });

    // Draw the route line between stops
    L.polyline(latlngs, { color: 'blue', weight: 3, opacity: 0.7 }).addTo(map);
    map.fitBounds(latlngs, { padding: [40, 40] });
  </script>
</body>
</html>
"""