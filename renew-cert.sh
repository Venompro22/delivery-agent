#!/bin/bash
# 🔐 Renews the certificate only when it's close to expiry (safe to run every day).
cd "$(dirname "$0")" && export PATH="$PWD/.venv/bin:$PATH"
certbot renew --config-dir certs/le --work-dir certs/le/work --logs-dir certs/le/logs \
  --manual-auth-hook "$PWD/certbot-hooks/ipv64-auth.sh" \
  --manual-cleanup-hook "$PWD/certbot-hooks/ipv64-cleanup.sh" \
  --deploy-hook "launchctl kickstart -k gui/$(id -u)/com.youssef.zhisong-server" "$@"
