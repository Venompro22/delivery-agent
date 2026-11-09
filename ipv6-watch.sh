#!/bin/bash
# 🌐 Keeps zhisong.home64.de pointing at this Mac's current IPv6 address. Fully automatic.
cd "$(dirname "$0")"
set -a; [ -f .env.local ] && . ./.env.local; set +a

DOMAIN=zhisong.home64.de
IP6=$(ifconfig en0 | awk '/inet6/ && /autoconf/ && !/temporary/ {print $2}' | head -1)
[ -z "$IP6" ] && exit 0                                   # offline: nothing to do

# Ask ipv64's own name server (no cache), so we only update when it really changed.
DNS=$(dig +short AAAA "$DOMAIN" @ns1.ipv64.net | head -1)
[ "$IP6" = "$DNS" ] && exit 0

R=$(curl -s -m 15 -G "https://ipv64.net/nic/update" --data-urlencode "key=$IPV64_KEY" \
      --data-urlencode "domain=$DOMAIN" --data-urlencode "ip6=$IP6")
if echo "$R" | grep -qE '"(good|nochg)"'; then
  osascript -e "display notification \"New address: $IP6\" with title \"zhisong: link updated\""
else
  osascript -e "display notification \"Update failed: $R\" with title \"zhisong: check needed\""
fi
