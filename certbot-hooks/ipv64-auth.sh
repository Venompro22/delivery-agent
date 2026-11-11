#!/bin/bash
# certbot DNS challenge: publish the TXT record through the ipv64 API, then wait until it's live.
cd "$(dirname "$0")/.." && set -a && . ./.env.local && set +a
curl -s -m 15 -X POST "https://ipv64.net/api.php" -H "Authorization: Bearer $IPV64_API" \
  --data-urlencode "add_record=$CERTBOT_DOMAIN" --data-urlencode "praefix=_acme-challenge" \
  --data-urlencode "type=TXT" --data-urlencode "content=$CERTBOT_VALIDATION" > /dev/null
for i in $(seq 1 30); do
  dig +short TXT "_acme-challenge.$CERTBOT_DOMAIN" @ns1.ipv64.net | grep -q "$CERTBOT_VALIDATION" && break
  sleep 5
done
sleep 20
