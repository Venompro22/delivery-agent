#!/bin/bash
# 💾 Daily backup of the delivery data (keeps the last 14 days)
set -e

APP=/home/scr-ysf2006/delivery-agent
DEST=/home/scr-ysf2006/backups
FILE="$DEST/delivery-$(date +%F).tgz"

mkdir -p "$DEST"
cd "$APP"
tar czf "$FILE" --ignore-failed-read orders.db feedback.jsonl
find "$DEST" -name "delivery-*.tgz" -mtime +14 -delete
echo "💾 Backup done: $FILE"
