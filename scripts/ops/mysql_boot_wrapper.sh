#!/bin/bash
# /Users/chenyiyun/PycharmProjects/Chenyiyun2087/scripts/ops/mysql_boot_wrapper.sh

# Wait for external volume mount
MAX_RETRIES=60
RETRY_COUNT=0
DATADIR="/Volumes/extension/mysql"
LOG_FILE="/tmp/mysql_boot.log"

echo "$(date): Starting MySQL boot wrapper" >> "$LOG_FILE"

while [ ! -d "$DATADIR" ] && [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    echo "$(date): Waiting for $DATADIR to be mounted... ($RETRY_COUNT/$MAX_RETRIES)" >> "$LOG_FILE"
    sleep 2
    RETRY_COUNT=$((RETRY_COUNT + 1))
done

if [ -d "$DATADIR" ]; then
    echo "$(date): Volume found. Starting MySQL..." >> "$LOG_FILE"
    
    # Run mysqld_safe
    export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    exec /opt/homebrew/opt/mysql/bin/mysqld_safe >> "$LOG_FILE" 2>&1
else
    echo "$(date): Error: Volume $DATADIR not found after $MAX_RETRIES retries." >> "$LOG_FILE"
    exit 1
fi
