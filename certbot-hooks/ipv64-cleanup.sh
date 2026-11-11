#!/bin/bash
# Remove the TXT record once Let's Encrypt has checked it.
cd "$(dirname "$0")/.." && set -a && . ./.env.local && set +a
curl -s -m 15 -X DELETE "https://ipv64.net/api.php" -H "Authorization: Bearer $IPV64_API" \
  --data-urlencode "del_record=$CERTBOT_DOMAIN" --data-urlencode "praefix=_acme-challenge" \
  --data-urlencode "type=TXT" --data-urlencode "content=$CERTBOT_VALIDATION" > /dev/null
