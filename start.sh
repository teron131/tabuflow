#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

API_HOST="${API_HOST:-localhost}"
API_PORT="${API_PORT:-8017}"
UI_HOST="${UI_HOST:-localhost}"
UI_PORT="${UI_PORT:-5174}"
export DATA_AGENTICS_API_URL="${DATA_AGENTICS_API_URL:-http://${API_HOST}:${API_PORT}}"

stop_port_listener() {
  local port="$1"
  local pids

  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    return
  fi

  kill $pids 2>/dev/null || true
  sleep 0.2
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    kill -9 $pids 2>/dev/null || true
  fi
}

cleanup() {
  if [[ -n "${API_PID:-}" ]]; then
    kill "$API_PID" 2>/dev/null || true
  fi
  if [[ -n "${UI_PID:-}" ]]; then
    kill "$UI_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

stop_port_listener "$API_PORT"
stop_port_listener "$UI_PORT"

uv run uvicorn src.api:app --host "$API_HOST" --port "$API_PORT" --reload &
API_PID=$!

(
  cd frontend
  pnpm dev --hostname "$UI_HOST" --port "$UI_PORT"
) &
UI_PID=$!

wait "$API_PID"
wait "$UI_PID"
