#!/bin/bash
# Run from the project folder with the project's venv, even when started by launchd.
cd "$(dirname "$0")"
export PATH="$PWD/.venv/bin:$PATH"
# Secrets live in .env.local (git-ignored), never in this file.
set -a
[ -f .env.local ] && . ./.env.local
set +a
# Local HTTPS dev server (mkcert certificate)
DEV_MODE=1 COOKIE_SECURE=1 \
  flask --app fileapp run \
  --host :: --port 5051 \
  --cert=certs/le/live/zhisong.duckdns.org/fullchain.pem --key=certs/le/live/zhisong.duckdns.org/privkey.pem
