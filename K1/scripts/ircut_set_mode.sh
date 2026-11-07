#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_DIR="$(cd "$BASE_DIR/.." && pwd)/runtime/state"
mkdir -p "$STATE_DIR"

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 {day|night|off|status} [extra args...]" >&2
  exit 1
fi

mode="$1"
shift || true

case "$mode" in
  day)
    python3 "$BASE_DIR/ir_cut_control.py" day "$@"
    echo day > "$STATE_DIR/current_mode"
    ;;
  night)
    python3 "$BASE_DIR/ir_cut_control.py" night "$@"
    echo night > "$STATE_DIR/current_mode"
    ;;
  off)
    python3 "$BASE_DIR/ir_cut_control.py" off "$@"
    echo off > "$STATE_DIR/current_mode"
    ;;
  status)
    python3 "$BASE_DIR/ir_cut_control.py" status "$@"
    if [ -f "$STATE_DIR/current_mode" ]; then
      echo "saved_mode=$(cat "$STATE_DIR/current_mode")"
    fi
    ;;
  *)
    echo "Unknown mode: $mode" >&2
    exit 1
    ;;
esac
