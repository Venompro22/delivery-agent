#!/bin/bash
# 💾 Pull the server backups to this Mac (so they survive if the server dies)
set -e
SERVER="scr-ysf2006@172.20.10.6"
DEST="$HOME/Backups/delivery-agent"

mkdir -p "$DEST"
echo "📥 Downloading backups from the server..."
rsync -a --ignore-existing "$SERVER:~/backups/" "$DEST/"
echo "✅ $(ls "$DEST" | wc -l | tr -d ' ') backups on this Mac:"
ls -lh "$DEST" | tail -5