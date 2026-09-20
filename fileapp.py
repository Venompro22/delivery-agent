"""Web dashboard for the delivery agent (Flask)."""

from __future__ import annotations

import time
from datetime import datetime

from flask import Flask, request, jsonify, render_template_string

from models import Order, Driver, OrderStatus
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
                "id": o.id,
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


@app.route("/api/orders/<order_id>/deliver", methods=["POST"])
def deliver_order(order_id):
    """Mark an order as delivered so it leaves the active route."""
    order = store.get(order_id)
    if order:
        order.status = OrderStatus.DELIVERED
        store.save()
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 404


# The dashboard HTML (map + form) lives here for now.
PAGE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Delivery Dashboard</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, "Segoe UI", sans-serif;
      margin: 0; display: flex; height: 100vh;
      background: #0f1115;
    }
    #sidebar {
      width: 340px; padding: 20px; overflow-y: auto;
      background: #1a1d24; color: #e8eaed;
      box-shadow: 2px 0 12px rgba(0,0,0,0.3);
    }
    .brand {
      display: flex; align-items: center; gap: 10px;
      padding-bottom: 16px; border-bottom: 1px solid #2a2e37;
      margin-bottom: 16px;
    }
    .brand .logo {
      width: 40px; height: 40px; border-radius: 10px;
      background: linear-gradient(135deg, #2d6cdf, #1f9d55);
      display: flex; align-items: center; justify-content: center;
      font-size: 22px; flex-shrink: 0;
    }
    .brand h2 { margin: 0; font-size: 18px; }
    .subtitle { color: #8a8f98; font-size: 12px; }
    #sidebar h3 {
      font-size: 13px; text-transform: uppercase; letter-spacing: 1px;
      color: #8a8f98; margin: 24px 0 12px;
    }
    .stats { display: flex; gap: 10px; margin: 16px 0; }
    .stat {
      flex: 1; background: #0f1115; border: 1px solid #2a2e37;
      border-radius: 10px; padding: 12px; text-align: center;
    }
    .stat .num { font-size: 22px; font-weight: 700; color: #2d6cdf; }
    .stat .label {
      font-size: 11px; color: #8a8f98; text-transform: uppercase;
      letter-spacing: 0.5px; margin-top: 2px;
    }
    #map { flex: 1; }
    input {
      width: 100%; padding: 11px; margin: 6px 0;
      background: #0f1115; border: 1px solid #2a2e37; border-radius: 8px;
      color: #e8eaed; font-size: 14px;
    }
    input:focus { outline: none; border-color: #2d6cdf; }
    input::placeholder { color: #5a5f68; }
    button {
      width: 100%; padding: 12px; background: #2d6cdf; color: white;
      border: none; border-radius: 8px; cursor: pointer; margin-top: 10px;
      font-size: 14px; font-weight: 600; transition: background 0.15s;
    }
    button:hover { background: #245bc0; }
    .stop {
      padding: 12px; margin-bottom: 8px; font-size: 14px;
      background: #0f1115; border: 1px solid #2a2e37; border-radius: 10px;
    }
    .stop .meta { color: #8a8f98; font-size: 12px; margin-top: 2px; }
    .stop button {
      width: auto; padding: 6px 12px; font-size: 12px;
      background: #1f9d55; margin-top: 8px; border-radius: 6px;
    }
    .stop button:hover { background: #178045; }
  </style>
</head>
<body>
  <div id="sidebar">
    <div class="brand">
      <div class="logo">🚚</div>
      <div>
        <h2>Delivery Agent</h2>
        <div class="subtitle">Smart routing dashboard</div>
      </div>
    </div>

    <input id="name" placeholder="Customer name"/>
    <input id="lat" placeholder="Latitude (e.g. 31.23)"/>
    <input id="lng" placeholder="Longitude (e.g. 121.47)"/>
    <input id="promise" placeholder="Promise in minutes (optional)"/>
    <button onclick="addOrder()">Add order</button>

    <div class="stats">
      <div class="stat"><div class="num" id="stat-stops">0</div><div class="label">Stops</div></div>
      <div class="stat"><div class="num" id="stat-km">0</div><div class="label">Total km</div></div>
    </div>

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
      document.getElementById('name').value = '';
      document.getElementById('lat').value = '';
      document.getElementById('lng').value = '';
      document.getElementById('promise').value = '';
      refresh();
    }

    async function deliver(id) {
      await fetch(`/api/orders/${id}/deliver`, {method: 'POST'});
      refresh();
    }

    async function refresh() {
      const res = await fetch('/api/route');
      const data = await res.json();
      layer.clearLayers();
      const pts = [[data.driver.lat, data.driver.lng]];
      L.circleMarker([data.driver.lat, data.driver.lng],
        {radius: 9, color: '#1f9d55', fillColor: '#1f9d55', fillOpacity: 1})
        .addTo(layer).bindPopup('Start');

      let html = '';
      data.stops.forEach(s => {
        pts.push([s.lat, s.lng]);
        L.circleMarker([s.lat, s.lng],
          {radius: 8, color: '#e5484d', fillColor: '#e5484d', fillOpacity: 1})
          .addTo(layer).bindPopup(`${s.sequence}. ${s.name} — ETA ${s.eta}`);
        html += `<div class="stop">
          <b>${s.sequence}. ${s.name}</b>
          <div class="meta">ETA ${s.eta} &middot; +${s.leg_km} km</div>
          <button onclick="deliver('${s.id}')">✓ Delivered</button>
        </div>`;
      });

      document.getElementById('stat-stops').textContent = data.summary.stops;
      document.getElementById('stat-km').textContent = data.summary.total_km;
      document.getElementById('stops').innerHTML = html;

      if (pts.length > 1) {
        L.polyline(pts, {color: '#2d6cdf', weight: 3, opacity: 0.7}).addTo(layer);
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