#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/home/ubuntu/vigil-backend}"
DB_PATH="${DB_PATH:-$APP_DIR/vigil.db}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/vigil}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DB_PATH" ]; then
  echo "[backup] database file not found: $DB_PATH"
  exit 1
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT_FILE="$BACKUP_DIR/vigil-$STAMP.sqlite3.gz"

python3 - "$DB_PATH" "$OUT_FILE" <<'PY'
import gzip
import sqlite3
import sys
import tempfile
from pathlib import Path

src = Path(sys.argv[1])
out = Path(sys.argv[2])

with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
  tmp_path = Path(tmp.name)

try:
  with sqlite3.connect(src) as source, sqlite3.connect(tmp_path) as destination:
    source.backup(destination)

  with open(tmp_path, "rb") as in_fh, gzip.open(out, "wb", compresslevel=6) as out_fh:
    out_fh.write(in_fh.read())
finally:
  tmp_path.unlink(missing_ok=True)

print(f"[backup] wrote {out}")
PY

find "$BACKUP_DIR" -type f -name 'vigil-*.sqlite3.gz' -mtime "+$KEEP_DAYS" -delete

echo "[backup] completed at $(date -Is)"
