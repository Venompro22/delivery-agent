#!/bin/bash
# Secrets live in .env.local (git-ignored), never in this file.
set -a
[ -f .env.local ] && . ./.env.local
set +a
# Local HTTPS dev server (mkcert certificate)
DEV_MODE=1 COOKIE_SECURE=1 \
  flask --app fileapp run \
  --host 0.0.0.0 --port 5051 \
  --cert=certs/localhost.pem --key=certs/localhost-key.pem \
  --debug
