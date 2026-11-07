#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${PROJECT_DIR}/runtime/logs"
PID_FILE="${PROJECT_DIR}/runtime/day_night_sync.pid"

mkdir -p "${LOG_DIR}"

if [[ -f "${PID_FILE}" ]]; then
  OLD_PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [[ -n "${OLD_PID}" ]] && kill -0 "${OLD_PID}" 2>/dev/null; then
    exit 0
  fi
fi

nohup python3 "${SCRIPT_DIR}/day_night.py" sync-ircut >> "${LOG_DIR}/sync_day_night_ircut.log" 2>&1 &
echo $! > "${PID_FILE}"
