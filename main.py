"""Run the delivery agent: build orders, optimize the route, print ETAs."""

import time
from datetime import datetime

from models import Order, Driver
from geo import GeoService
from optimizer import optimize_route, route_summary


def format_time(epoch):
    return datetime.fromtimestamp(epoch).strftime("%H:%M") if epoch else "--"


def main():
    driver = Driver(name="Driver 1", lat=31.2304, lng=121.4737)
    geo = GeoService(avg_speed_kmh=driver.avg_speed_kmh)

    orders = [
        Order("Customer A", 31.2397, 121.4998),  # far, no promise
        Order("Customer B", 31.2290, 121.4750),  # near, no promise
        Order("Customer C", 31.2240, 121.4450,   # mid, URGENT (15 min!)
              promised_by=time.time() + 15 * 60),
    ]

    route = optimize_route(driver, orders, geo)
    summary = route_summary(route)

    print(f"\nRoute for {driver.name} — {summary['stops']} stops, "
          f"{summary['total_km']} km\n")
    for order in route:
        promise = ""
        if order.promised_by:
            promise = f"  (promised {format_time(order.promised_by)})"
        print(f"{order.sequence}. {order.customer_name:<12} "
              f"ETA {format_time(order.eta_epoch)}  "
              f"(+{order.leg_distance_km} km){promise}")
    from mapview import render_map
    import webbrowser, os
    path = render_map(driver, route)
    print(f"\n🗺️  Map saved to {path} — opening...")
    webbrowser.open("file://" + os.path.abspath(path))

if __name__ == "__main__":
    main()

