"""PWA files: the web app manifest and the service worker."""
from __future__ import annotations

from flask import send_from_directory
import json
import os

import fileapp as core


# ---------- Installable app (PWA): manifest and service worker ----------
@core.app.route("/manifest.webmanifest")
def web_manifest():
    """What the phone needs to install the app: name, colours, icons, start page."""
    data = {
        "name": "智送管家", "short_name": "智送管家",
        "description": "Smart delivery: AI orders, routes and live tracking",
        "start_url": "/", "scope": "/", "display": "standalone", "lang": "zh-CN",
        "background_color": "#0b1220", "theme_color": "#1f5fd6",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/icons/maskable-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
    }
    resp = core.app.response_class(json.dumps(data, ensure_ascii=False), mimetype="application/manifest+json")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@core.app.route("/sw.js")
def service_worker():
    """Served from the root so it can look after every page of the app."""
    resp = send_from_directory(os.path.join(core.HERE, "static"), "sw.js", max_age=0)
    resp.headers["Content-Type"] = "application/javascript; charset=utf-8"
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp
