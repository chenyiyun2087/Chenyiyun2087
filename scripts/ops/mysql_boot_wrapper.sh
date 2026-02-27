#!/bin/bash
# /Users/chenyiyun/PycharmProjects/Chenyiyun2087/scripts/ops/mysql_boot_wrapper.sh

# Wait for external volume mount
MAX_RETRIES=120
RETRY_COUNT=0
SLEEP_SECONDS=2

DATADIR="/Volumes/extension/mysql"
LOG_FILE="/tmp/mysql_boot.log"

# IMPORTANT: Put err/pid/socket on local writable path (not external volume)
MYSQL_ERR_LOG="/tmp/mysql_external.err"
MYSQL_PID_FILE="/tmp/mysql_external.pid"
MYSQL_SOCKET="/tmp/mysql_external.sock"
MYSQL_RUN_USER="_mysql"
MYSQLD_SAFE="/opt/homebrew/opt/mysql/bin/mysqld_safe"
CURRENT_UID="$(id -u)"

# Basic environment (launchd/cron env is minimal)
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="/Users/chenyiyun"
export USER="chenyiyun"
umask 022

echo "==================================================" >> "$LOG_FILE"
echo "$(date): Starting MySQL boot wrapper" >> "$LOG_FILE"
echo "$(date): DATADIR=$DATADIR" >> "$LOG_FILE"
echo "$(date): LOG=$MYSQL_ERR_LOG PID=$MYSQL_PID_FILE SOCK=$MYSQL_SOCKET" >> "$LOG_FILE"

# Wait until external data directory appears
while [ ! -d "$DATADIR" ] && [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    echo "$(date): Waiting for $DATADIR to be mounted... ($RETRY_COUNT/$MAX_RETRIES)" >> "$LOG_FILE"
    sleep "$SLEEP_SECONDS"
    RETRY_COUNT=$((RETRY_COUNT + 1))
done

if [ ! -d "$DATADIR" ]; then
    echo "$(date): ERROR: Volume $DATADIR not found after $MAX_RETRIES retries." >> "$LOG_FILE"
    exit 1
fi

echo "$(date): Volume found. Ensuring permissions..." >> "$LOG_FILE"
if [ "$CURRENT_UID" -eq 0 ]; then
  chown -R "$MYSQL_RUN_USER":staff "$DATADIR" >> "$LOG_FILE" 2>&1 || true
fi

# Clean stale local pid/socket/log (safe to ignore errors)
rm -f "$MYSQL_PID_FILE" "$MYSQL_SOCKET" >> "$LOG_FILE" 2>&1

# Ensure error log is writable by the MySQL run user
if [ "$CURRENT_UID" -eq 0 ]; then
  touch "$MYSQL_ERR_LOG" >> "$LOG_FILE" 2>&1 || true
  chown "$MYSQL_RUN_USER":staff "$MYSQL_ERR_LOG" >> "$LOG_FILE" 2>&1 || true
  chmod 640 "$MYSQL_ERR_LOG" >> "$LOG_FILE" 2>&1 || true
else
  touch "$MYSQL_ERR_LOG" >> "$LOG_FILE" 2>&1 || true
fi

# Optional: try removing stale datadir pid if launchd/cron context can access it
# (ignore failure; this is only best-effort)
for f in "$DATADIR"/*.pid; do
    [ -e "$f" ] || continue
    rm -f "$f" >> "$LOG_FILE" 2>&1 || true
done

MYSQLD_CMD="$MYSQLD_SAFE --datadir=\"$DATADIR\" --log-error=\"$MYSQL_ERR_LOG\" --pid-file=\"$MYSQL_PID_FILE\" --socket=\"$MYSQL_SOCKET\" --user=\"$MYSQL_RUN_USER\""
echo "$(date): CMD=$MYSQLD_CMD (run as $MYSQL_RUN_USER)" >> "$LOG_FILE"

MYSQL_RUN_UID="$(id -u "$MYSQL_RUN_USER" 2>/dev/null || true)"

if [ -z "$MYSQL_RUN_UID" ]; then
  echo "$(date): ERROR: user $MYSQL_RUN_USER not found." >> "$LOG_FILE"
  exit 1
fi

if [ "$CURRENT_UID" -eq 0 ]; then
  # Run directly as root; mysqld_safe will drop to --user=_mysql internally.
  exec /bin/sh -c "$MYSQLD_CMD" >> "$LOG_FILE" 2>&1
elif [ "$CURRENT_UID" -eq "$MYSQL_RUN_UID" ]; then
  # Already running as mysql user.
  exec $MYSQLD_SAFE \
    --datadir="$DATADIR" \
    --log-error="$MYSQL_ERR_LOG" \
    --pid-file="$MYSQL_PID_FILE" \
    --socket="$MYSQL_SOCKET" \
    --user="$MYSQL_RUN_USER" \
    >> "$LOG_FILE" 2>&1
else
  echo "$(date): ERROR: run this wrapper as root (sudo) or $MYSQL_RUN_USER." >> "$LOG_FILE"
  exit 1
fi
