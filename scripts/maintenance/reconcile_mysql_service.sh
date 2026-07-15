#!/bin/bash
set -euo pipefail

# Reconcile duplicate Homebrew MySQL launch jobs without stopping the healthy
# mysqld that currently owns the data directory.  Dry-run is the default.

MODE="dry-run"
if [ "${1:-}" = "--execute" ]; then
  MODE="execute"
elif [ -n "${1:-}" ] && [ "${1:-}" != "--dry-run" ]; then
  echo "usage: sudo $0 [--dry-run|--execute]" >&2
  exit 64
fi

DATADIR="/Volumes/extension/mysql"
USER_UID="${SUDO_UID:-$(id -u)}"
USER_HOME="$(dscl . -read "/Users/$(stat -f '%Su' /dev/console)" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
USER_HOME="${USER_HOME:-/Users/chenyiyun}"
USER_PLIST="$USER_HOME/Library/LaunchAgents/homebrew.mxcl.mysql.plist"
SYSTEM_PLIST="/Library/LaunchDaemons/homebrew.mxcl.mysql.plist"

snapshot() {
  echo "mysqld processes:"
  ps -axo pid,ppid,user,lstart,command | grep -E '[/]mysqld(_safe)?' | grep -v grep || true
  echo "datadir owners:"
  pgrep -af "[/]mysqld --.*--datadir=$DATADIR([[:space:]]|$)" || true
  echo "launch jobs:"
  launchctl print "gui/$USER_UID/homebrew.mxcl.mysql" 2>/dev/null | sed -n '1,24p' || true
  launchctl print system/homebrew.mxcl.mysql 2>/dev/null | sed -n '1,24p' || true
}

snapshot
if [ "$MODE" = "dry-run" ]; then
  echo "dry-run: would disable the user job and boot out the failing system launcher."
  echo "The healthy manually started mysqld is not signalled by this script."
  exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: --execute requires sudo/root." >&2
  exit 77
fi

# Prevent a second user-domain launch job.  Ignore an already-unloaded job.
launchctl bootout "gui/$USER_UID" "$USER_PLIST" 2>/dev/null || true
launchctl disable "gui/$USER_UID/homebrew.mxcl.mysql" 2>/dev/null || true

# The system job is currently the failed duplicate. Booting out its launchd
# parent does not signal the unrelated manual mysqld that owns the datadir.
launchctl bootout system "$SYSTEM_PLIST" 2>/dev/null || true
launchctl disable system/homebrew.mxcl.mysql 2>/dev/null || true

sleep 2
owners="$(pgrep -f "[/]mysqld --.*--datadir=$DATADIR([[:space:]]|$)" | wc -l | tr -d ' ')"
if [ "$owners" -ne 1 ]; then
  echo "ERROR: expected exactly one mysqld owner after reconciliation; found $owners." >&2
  snapshot
  exit 1
fi

echo "reconciliation complete; one datadir owner remains."
snapshot
