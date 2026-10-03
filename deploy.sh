#!/usr/bin/env bash
# Deploy the delivery agent to the Kali server in one command.
set -euo pipefail

SERVER="scr-ysf2006@172.20.10.6"
REMOTE_DIR="delivery-agent"
FILES=(fileapp.py geo.py models.py optimizer.py store.py order_parser.py requirements.txt)
echo "🔎 1/4 Checking Python code..."
python3 -m py_compile fileapp.py geo.py models.py optimizer.py store.py order_parser.py
echo "🧪    Testing route logic with a fake order..."
python3 -c "
from geo import GeoService
from models import Order, Driver
from optimizer import optimize_route
d = Driver(name='t', lat=30.27, lng=120.15)
r = optimize_route(d, [Order(customer_name='t', lat=30.13, lng=120.26)], GeoService())
assert len(r) == 1, 'route is empty'
print('    ✅ route logic OK')
"
echo "📤 2/4 Uploading files..."
scp -q "${FILES[@]}" "$SERVER:$REMOTE_DIR/"

echo "🔄 3/4 Restarting service..."
ssh "$SERVER" "sudo /usr/bin/systemctl restart delivery"
sleep 2

echo "🌐 4/4 Checking the site..."
code=$(curl --noproxy '*' -s -o /dev/null -w "%{http_code}" "http://172.20.10.6/login")
if [ "$code" = "200" ]; then
  echo "✅ Deployed! Site is up."
else
  echo "❌ Site returned $code — check: ssh $SERVER 'journalctl -u delivery -n 20'"
  exit 1
fi