#!/bin/bash
# Local HTTPS dev server (mkcert certificate)
DEV_MODE=1 DASHBOARD_PASSWORD=test COOKIE_SECURE=1 \
  flask --app fileapp run \
  --host 0.0.0.0 --port 5051 \
  --cert=certs/localhost.pem --key=certs/localhost-key.pem \
  --debug
