#!/bin/bash
set -euo pipefail

role="${1:-}"
case "$role" in
  web) launcher_name="web_console_launcher.py" ;;
  worker) launcher_name="task_worker_launcher.py" ;;
  *) echo "FATAL: service role must be web or worker" >&2; exit 64 ;;
esac

service_home="$HOME/Library/Application Support/Chenyiyun2087"
manifest="${CHENYIYUN_RELEASE_MANIFEST:-$service_home/production_release.json}"
release_root="$(/usr/bin/plutil -extract project_root raw -o - "$manifest")"
launcher="$release_root/scripts/ops/$launcher_name"
python_app="/opt/homebrew/opt/python@3.14/Frameworks/Python.framework/Versions/3.14/Resources/Python.app"
runtime_dir="$service_home/runtime"
log_dir="$HOME/Library/Logs/Chenyiyun2087"
mkdir -p "$runtime_dir" "$log_dir"
chmod 700 "$runtime_dir"
pid_file="$runtime_dir/$role.$$.pid"
open_pid=""

terminate_service() {
  trap - TERM INT
  if [ -s "$pid_file" ]; then
    read -r runtime_pid < "$pid_file"
    if [[ "$runtime_pid" =~ ^[0-9]+$ ]] && [ "$runtime_pid" -gt 1 ]; then
      runtime_uid="$(/bin/ps -p "$runtime_pid" -o uid= | tr -d ' ' || true)"
      if [ "$runtime_uid" = "$(id -u)" ]; then
        kill -TERM "$runtime_pid" 2>/dev/null || true
      fi
    fi
  fi
  if [ -n "$open_pid" ]; then
    wait "$open_pid" || true
  fi
  exit 0
}
trap terminate_service TERM INT
trap 'rm "$pid_file" 2>/dev/null || true' EXIT

# LaunchServices supplies the desktop launch context; -W keeps this supervisor
# alive for the application lifetime. The PID survives the launcher's exec.
/usr/bin/open -n -g -j -W -a "$python_app" \
  --stdout "$log_dir/$role-runtime.stdout.log" \
  --stderr "$log_dir/$role-runtime.stderr.log" \
  --env "CHENYIYUN_SERVICE_PID_FILE=$pid_file" \
  --env "CHENYIYUN_RELEASE_MANIFEST=$manifest" \
  --env "CHENYIYUN_REQUIRE_RELEASE=1" \
  --args "$launcher" &
open_pid=$!
wait "$open_pid"
