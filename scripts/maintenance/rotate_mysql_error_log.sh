#!/bin/bash
set -euo pipefail

LOG_FILE="${MYSQL_ERROR_LOG:-/Users/Shared/mysql_external.err}"
ARCHIVE_DIR="${MYSQL_ERROR_ARCHIVE_DIR:-/Users/Shared/mysql-log-archive}"
MAX_BYTES="${MYSQL_ERROR_MAX_BYTES:-104857600}"
KEEP="${MYSQL_ERROR_ARCHIVE_KEEP:-7}"
TAIL_LINES="${MYSQL_ERROR_EVIDENCE_LINES:-5000}"

if [ ! -f "$LOG_FILE" ]; then
  echo "log not found: $LOG_FILE"
  exit 0
fi

size="$(stat -f '%z' "$LOG_FILE")"
if [ "$size" -lt "$MAX_BYTES" ]; then
  echo "rotation not needed: $size bytes < $MAX_BYTES bytes"
  exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: rotation requires sudo/root to preserve MySQL log ownership." >&2
  exit 77
fi

mkdir -p "$ARCHIVE_DIR"
stamp="$(date '+%Y%m%d_%H%M%S')"
archive="$ARCHIVE_DIR/mysql_external.$stamp.tail.log"
tail -n "$TAIL_LINES" "$LOG_FILE" > "$archive"
gzip -f "$archive"
: > "$LOG_FILE"
chown _mysql:staff "$LOG_FILE"
chmod 640 "$LOG_FILE"

find "$ARCHIVE_DIR" -type f -name 'mysql_external.*.tail.log.gz' -print0 \
  | xargs -0 ls -1t 2>/dev/null \
  | tail -n "+$((KEEP + 1))" \
  | xargs -I{} rm -f "{}"

echo "rotated $size bytes; evidence saved to $archive.gz"

