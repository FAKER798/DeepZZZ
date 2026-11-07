#!/usr/bin/env bash
set -Eeuo pipefail

DEMO_DIR="${DEMO_DIR:-/home/z/pose_demo}"

for name in viewer infer camera; do
  pidfile="$DEMO_DIR/$name.pid"
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  fi
done

if [ -f "$DEMO_DIR/ultralytics_pose.pid" ]; then
  pid="$(cat "$DEMO_DIR/ultralytics_pose.pid" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
  fi
  rm -f "$DEMO_DIR/ultralytics_pose.pid"
fi

sleep 1

for name in viewer infer camera; do
  pidfile="$DEMO_DIR/$name.pid"
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
    rm -f "$pidfile"
  fi
done

pkill -u "$(id -u)" -f "/home/z/pose_demo/pose_result_viewer.py" >/dev/null 2>&1 || true
pkill -u "$(id -u)" -f "/home/z/pose_demo/pose_live_viewer.py" >/dev/null 2>&1 || true
pkill -u "$(id -u)" -f "/home/z/pose_demo/pose_ultralytics_spacemit.py" >/dev/null 2>&1 || true
pkill -u "$(id -u)" -f "jobot_usb_cam_node_exe" >/dev/null 2>&1 || true
pkill -u "$(id -u)" -f "infer_video.launch.py" >/dev/null 2>&1 || true
pkill -u "$(id -u)" -f "infer_video_node" >/dev/null 2>&1 || true

echo "Stopped YOLOv8n-Pose demo."
