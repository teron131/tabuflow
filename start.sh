#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

API_HOST="${API_HOST:-localhost}"
API_PORT="${API_PORT:-8017}"
UI_HOST="${UI_HOST:-localhost}"
UI_PORT="${UI_PORT:-5174}"
export DATA_AGENTICS_API_URL="${DATA_AGENTICS_API_URL:-http://${API_HOST}:${API_PORT}}"

cleanup() {
  if [[ -n "${API_PID:-}" ]]; then
    kill "$API_PID" 2>/dev/null || true
  fi
  if [[ -n "${UI_PID:-}" ]]; then
    kill "$UI_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

uv run uvicorn src.api:app --host "$API_HOST" --port "$API_PORT" --reload &
API_PID=$!

pnpm --dir frontend dev --hostname "$UI_HOST" --port "$UI_PORT" &
UI_PID=$!

wait "$API_PID"
wait "$UI_PID"
