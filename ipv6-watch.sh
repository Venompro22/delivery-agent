#!/bin/bash
# 🌐 Keeps zhisong.duckdns.org pointing at this Mac's current IPv6 address.
cd "$(dirname "$0")"
set -a; [ -f .env.local ] && . ./.env.local; set +a

DOMAIN=zhisong
STATE=.ipv6_last          # last address we already tried, so we don't nag every 5 min

IP6=$(ifconfig en0 | awk '/inet6/ && /autoconf/ && !/temporary/ {print $2}' | head -1)
[ -z "$IP6" ] && exit 0   # offline: nothing to do

DNS=$(dig +short AAAA "$DOMAIN.duckdns.org" @223.5.5.5 | head -1)
if [ "$IP6" = "$DNS" ]; then rm -f "$STATE"; exit 0; fi   # all good
[ "$IP6" = "$(cat "$STATE" 2>/dev/null)" ] && exit 0      # already handled this one
echo "$IP6" > "$STATE"

URL="https://www.duckdns.org/update?domains=$DOMAIN&token=$DUCKDNS_TOKEN&ipv6=$IP6"
if [ "$(curl -s -m 10 "$URL")" = "OK" ]; then
  osascript -e "display notification \"New address: $IP6\" with title \"zhisong: link updated\""
else
  # DuckDNS is blocked without VPN here: let the browser (with VPN) do it.
  osascript -e 'display notification "IPv6 changed. A tab opened to fix the link — keep the VPN on." with title "zhisong: action needed"'
  open "$URL"
fi
