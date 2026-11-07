#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE_DIR"

PORT="${PORT:-7860}"
HOST="${HOST:-0.0.0.0}"

mkdir -p runtime/logs

if [ -f runtime/app.pid ]; then
  OLD_PID="$(cat runtime/app.pid)"
  if kill -0 "$OLD_PID" 2>/dev/null && ps -p "$OLD_PID" -o stat= | grep -vq Z; then
    echo "DeepZZZ K1 is already running with PID $OLD_PID"
    exit 0
  fi
  rm -f runtime/app.pid
fi

nohup python3 main.py --host "$HOST" --port "$PORT" "$@" \
  > runtime/logs/app.out.log 2> runtime/logs/app.err.log &
echo "$!" > runtime/app.pid

sleep 1
IP="$(hostname -I | awk '{print $1}')"
echo "DeepZZZ K1 URL: http://${IP}:${PORT}/"
echo "PID: $(cat runtime/app.pid)"
