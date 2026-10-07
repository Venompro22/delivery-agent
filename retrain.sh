#!/bin/bash
# 🎡 Data flywheel: pull feedback from the server → retrain → deploy the smarter brain
set -e
SERVER="scr-ysf2006@172.20.10.6"

echo "📥 1/3 Pulling feedback from the server..."
scp -q "$SERVER:~/delivery-agent/feedback.jsonl" . 2>/dev/null || echo "    (no feedback yet)"

echo "🏋️ 2/3 Retraining intent_nn..."
python3 intent_nn.py | grep -E "📚|🧪 Accuracy|🎓"

echo "🚀 3/3 Deploying the new brain..."
./deploy.sh