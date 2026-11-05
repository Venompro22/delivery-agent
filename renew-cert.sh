#!/bin/bash
# 🔐 Renew the Let's Encrypt certificate (every ~80 days). Same manual DNS steps as the first time.
cd "$(dirname "$0")"
certbot certonly --manual --preferred-challenges dns -d zhisong.duckdns.org \
  --config-dir certs/le --work-dir certs/le/work --logs-dir certs/le/logs \
  --register-unsafely-without-email --agree-tos
launchctl kickstart -k gui/$(id -u)/com.youssef.zhisong-server
