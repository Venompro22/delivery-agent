"""Web dashboard for the delivery agent (Flask)."""

from __future__ import annotations

import time
from datetime import datetime

from flask import Flask, request, jsonify, render_template_string

from models import Order, Driver
from geo import GeoService
from optimizer import optimize_route, route_summary
from store import OrderStore

app = Flask(__name__)

# One driver + a persistent order store (saved to orders.json).
driver = Driver(name="Driver 1", lat=31.2304, lng=121.4737)
geo = GeoService(avg_speed_kmh=driver.avg_speed_kmh)
store = OrderStore()


def fmt(epoch):
    return datetime.fromtimestamp(epoch).strftime("%H:%M") if epoch else "--"


@app.route("/")
def home():
    """Serve the dashboard page."""
    return render_template_string(PAGE)


@app.route("/api/route")
def api_route():
    """Return the optimized route as JSON for the map/list."""
    route = optimize_route(driver, store.all(), geo)
    summary = route_summary(route)
    data = {
        "driver": {"lat": driver.lat, "lng": driver.lng, "name": driver.name},
        "summary": summary,
        "stops": [
            {
                "sequence": o.sequence,
                "name": o.customer_name,
                "lat": o.lat,
                "lng": o.lng,
                "eta": fmt(o.eta_epoch),
                "leg_km": o.leg_distance_km,
            }
            for o in route
        ],
    }
    return jsonify(data)


@app.route("/api/orders", methods=["POST"])
def add_order():
    """Add a new order from the form."""
    body = request.get_json()
    kw = {}
    if body.get("promise_minutes"):
        kw["promised_by"] = time.time() + float(body["promise_minutes"]) * 60
    order = Order(
        customer_name=body["name"],
        lat=float(body["lat"]),
        lng=float(body["lng"]),
        **kw,
    )
    store.add(order)
    return jsonify({"ok": True, "id": order.id})


# The dashboard HTML (map + form) lives here for now.
PAGE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Delivery Dashboard</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    body { font-family: sans-serif; margin: 0; display: flex; height: 100vh; }
    #sidebar { width: 320px; padding: 16px; background: #111; color: #eee;
               overflow-y: auto; }
    #map { flex: 1; }
    input { width: 100%; padding: 8px; margin: 4px 0; box-sizing: border-box; }
    button { width: 100%; padding: 10px; background: #2d6cdf; color: white;
             border: none; border-radius: 6px; cursor: pointer; margin-top: 8px; }
    .stop { padding: 8px; border-bottom: 1px solid #333; font-size: 14px; }
  </style>
</head>
<body>
  <div id="sidebar">
    <h2>🚚 Dashboard</h2>
    <input id="name" placeholder="Customer name"/>
    <input id="lat" placeholder="Latitude (e.g. 31.23)"/>
    <input id="lng" placeholder="Longitude (e.g. 121.47)"/>
    <input id="promise" placeholder="Promise in minutes (optional)"/>
    <button onclick="addOrder()">Add order</button>
    <h3>Route</h3>
    <div id="stops"></div>
  </div>
  <div id="map"></div>

  <script>
    const map = L.map('map').setView([31.2304, 121.4737], 13);
    // Gaode / AutoNavi tiles — work reliably inside China (no VPN needed).
    L.tileLayer('https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}', {
      subdomains: ['1', '2', '3', '4'],
      attribution: '&copy; AutoNavi'
    }).addTo(map);
    let layer = L.layerGroup().addTo(map);

    async function addOrder() {
      const body = {
        name: document.getElementById('name').value,
        lat: document.getElementById('lat').value,
        lng: document.getElementById('lng').value,
        promise_minutes: document.getElementById('promise').value,
      };
      await fetch('/api/orders', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      refresh();
    }

    async function refresh() {
      const res = await fetch('/api/route');
      const data = await res.json();
      layer.clearLayers();
      const pts = [[data.driver.lat, data.driver.lng]];
      L.circleMarker([data.driver.lat, data.driver.lng],
        {radius: 9, color: 'green'}).addTo(layer).bindPopup('Start');

      let html = '';
      data.stops.forEach(s => {
        pts.push([s.lat, s.lng]);
        L.circleMarker([s.lat, s.lng], {radius: 8, color: 'red'})
          .addTo(layer).bindPopup(`${s.sequence}. ${s.name} — ETA ${s.eta}`);
        html += `<div class="stop">${s.sequence}. ${s.name} — ETA ${s.eta}</div>`;
      });
      document.getElementById('stops').innerHTML = html;
      if (pts.length > 1) {
        L.polyline(pts, {color: 'blue'}).addTo(layer);
        map.fitBounds(pts, {padding: [40, 40]});
      }
    }

    refresh();
  </script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(debug=True, port=5000)