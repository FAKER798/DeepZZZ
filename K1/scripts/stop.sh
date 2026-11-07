#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE_DIR"

if [ -f runtime/app.pid ]; then
  PID="$(cat runtime/app.pid)"
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    for _ in $(seq 1 30); do
      kill -0 "$PID" 2>/dev/null || break
      sleep 0.1
    done
    kill -0 "$PID" 2>/dev/null && kill -9 "$PID" || true
  fi
  rm -f runtime/app.pid
fi

pkill -f "ffmpeg .*image2pipe" 2>/dev/null || true
echo "DeepZZZ K1 stopped"
