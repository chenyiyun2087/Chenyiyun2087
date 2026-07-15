#!/bin/bash
# /Users/chenyiyun/PycharmProjects/Chenyiyun2087/scripts/ops/mysql_boot_wrapper.sh

# Wait for external volume mount
MAX_RETRIES=120
RETRY_COUNT=0
SLEEP_SECONDS=2

DATADIR="/Volumes/extension/mysql"
LOG_FILE="/tmp/mysql_boot.log"

# IMPORTANT: Put err/pid/socket on local writable path (not external volume)
MYSQL_ERR_LOG="/Users/Shared/mysql_external.err"
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

# This wrapper is intended for the system LaunchDaemon.  A user LaunchAgent
# with KeepAlive would otherwise fail and respawn forever, filling the error
# log while the real server continues to run.
MYSQL_RUN_UID="$(id -u "$MYSQL_RUN_USER" 2>/dev/null || true)"
if [ -z "$MYSQL_RUN_UID" ]; then
  echo "$(date): ERROR: user $MYSQL_RUN_USER not found." >> "$LOG_FILE"
  exit 1
fi
if [ "$CURRENT_UID" -ne 0 ] && [ "$CURRENT_UID" -ne "$MYSQL_RUN_UID" ]; then
  echo "$(date): ERROR: run this wrapper as root or $MYSQL_RUN_USER; refusing restart loop." >> "$LOG_FILE"
  exit 78
fi

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

# Never start a second server against the same InnoDB data directory.  Do this
# before touching pid/socket files: they may belong to a healthy manual or
# system-managed instance.  Avoid recursive chown on a 200+ GB warehouse.
if pgrep -f "[/]mysqld.*--datadir=$DATADIR([[:space:]]|$)" >/dev/null 2>&1; then
  echo "$(date): MySQL already owns $DATADIR; startup skipped." >> "$LOG_FILE"
  exit 0
fi

if [ "$CURRENT_UID" -eq 0 ]; then
  chown "$MYSQL_RUN_USER":staff "$DATADIR" >> "$LOG_FILE" 2>&1 || true
fi

# Only stale files may be removed after confirming no server owns the datadir.
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

echo "$(date): Starting $MYSQLD_SAFE (run as $MYSQL_RUN_USER)" >> "$LOG_FILE"

if [ "$CURRENT_UID" -eq 0 ]; then
  # Run directly as root; mysqld_safe will drop to --user=_mysql internally.
  exec "$MYSQLD_SAFE" \
    --datadir="$DATADIR" \
    --log-error="$MYSQL_ERR_LOG" \
    --pid-file="$MYSQL_PID_FILE" \
    --socket="$MYSQL_SOCKET" \
    --user="$MYSQL_RUN_USER" \
    >> "$LOG_FILE" 2>&1
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
