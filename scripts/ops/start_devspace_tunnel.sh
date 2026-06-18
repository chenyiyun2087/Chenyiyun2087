#!/usr/bin/env bash
set -euo pipefail

LOCAL_HOST="${HOST:-127.0.0.1}"
LOCAL_PORT="${PORT:-7676}"
LOCAL_URL="http://${LOCAL_HOST}:${LOCAL_PORT}"
LOG_FILE="$(mktemp -t devspace-cloudflared.XXXXXX.log)"
CLOUDFLARED_PID=""

cleanup() {
  if [[ -n "${CLOUDFLARED_PID}" ]] && kill -0 "${CLOUDFLARED_PID}" 2>/dev/null; then
    kill "${CLOUDFLARED_PID}" 2>/dev/null || true
    wait "${CLOUDFLARED_PID}" 2>/dev/null || true
  fi
  rm -f "${LOG_FILE}"
}
trap cleanup EXIT INT TERM

require_command() {
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Missing required command: ${command_name}" >&2
    exit 1
  fi
}

require_command cloudflared
require_command devspace

cloudflared tunnel --url "${LOCAL_URL}" >"${LOG_FILE}" 2>&1 &
CLOUDFLARED_PID="$!"

PUBLIC_URL=""
for _ in {1..60}; do
  if ! kill -0 "${CLOUDFLARED_PID}" 2>/dev/null; then
    cat "${LOG_FILE}" >&2
    exit 1
  fi

  PUBLIC_URL="$(grep -Eo 'https://[-a-zA-Z0-9]+\.trycloudflare\.com' "${LOG_FILE}" | head -n 1 || true)"
  if [[ -n "${PUBLIC_URL}" ]]; then
    break
  fi

  sleep 1
done

if [[ -z "${PUBLIC_URL}" ]]; then
  echo "Timed out waiting for Cloudflare Tunnel URL." >&2
  cat "${LOG_FILE}" >&2
  exit 1
fi

devspace config set publicBaseUrl "${PUBLIC_URL}" >/dev/null

echo "DevSpace public MCP URL: ${PUBLIC_URL}/mcp"
echo "Owner password is stored at: ${HOME}/.devspace/auth.json"
echo "Press Ctrl-C to stop DevSpace and the tunnel."

devspace serve
