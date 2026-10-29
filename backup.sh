#!/bin/bash
# 💾 Daily backup of the delivery app: database + delivery photos.
# Kept outside the project folder, so deleting the project doesn't take the backups with it.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${BACKUP_DIR:-$HOME/delivery-backups}"
KEEP_DAYS=7
STAMP="$(date +%Y-%m-%d_%H%M)"

mkdir -p "$DEST"

# SQLite must be copied with .backup, not cp: a plain copy during a write
# can produce a corrupt file.
sqlite3 "$HERE/orders.db" ".backup '$DEST/orders_$STAMP.db'"

# Delivery photos (skip silently if there are none yet)
if [ -d "$HERE/proofs" ] && [ -n "$(ls -A "$HERE/proofs" 2>/dev/null)" ]; then
  tar -czf "$DEST/proofs_$STAMP.tar.gz" -C "$HERE" proofs
fi

# Drop anything older than KEEP_DAYS
find "$DEST" -name 'orders_*.db' -mtime +$KEEP_DAYS -delete
find "$DEST" -name 'proofs_*.tar.gz' -mtime +$KEEP_DAYS -delete

echo "Backup done: $DEST (kept $KEEP_DAYS days)"
ls -lh "$DEST" | tail -5
