#!/usr/bin/env bash
set -Eeuo pipefail

DEMO_DIR="${DEMO_DIR:-/home/z/pose_demo}"
RESULT_TOPIC="${RESULT_TOPIC:-/result_img}"
IMAGE_TOPIC="${IMAGE_TOPIC:-/image_raw}"
POSE_TOPIC="${POSE_TOPIC:-/inference_pose_result}"
LOG_DIR="$DEMO_DIR/logs"
DISPLAY_VALUE="${DISPLAY:-:0}"
QT_PLATFORM_VALUE="${QT_QPA_PLATFORM:-xcb}"

mkdir -p "$LOG_DIR"
cd "$DEMO_DIR"

if [ -f viewer.pid ]; then
  old_pid="$(cat viewer.pid 2>/dev/null || true)"
  if [ -n "$old_pid" ] && kill -0 "$old_pid" >/dev/null 2>&1; then
    kill "$old_pid" >/dev/null 2>&1 || true
  fi
  rm -f viewer.pid
fi

pkill -u "$(id -u)" -f "/home/z/pose_demo/pose_result_viewer.py" >/dev/null 2>&1 || true
pkill -u "$(id -u)" -f "/home/z/pose_demo/pose_live_viewer.py" >/dev/null 2>&1 || true
sleep 1

nohup env \
  DISPLAY="$DISPLAY_VALUE" \
  QT_QPA_PLATFORM="$QT_PLATFORM_VALUE" \
  XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}" \
  DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/1000/bus}" \
  RESULT_TOPIC="$RESULT_TOPIC" \
  IMAGE_TOPIC="$IMAGE_TOPIC" \
  POSE_TOPIC="$POSE_TOPIC" \
  bash -lc '
    set +u
    source /opt/ros/humble/setup.bash
    source /opt/bros/humble/setup.bash
    set -u
    unset WAYLAND_DISPLAY
    exec python3 /home/z/pose_demo/pose_live_viewer.py --ros-args \
      -p image_topic:="$IMAGE_TOPIC" \
      -p pose_topic:="$POSE_TOPIC" \
      -p publish_topic:="$RESULT_TOPIC" \
      -p publish_result_img:=false
  ' >"$LOG_DIR/viewer.log" 2>&1 &

echo "$!" > viewer.pid
echo "Restarted viewer on $DISPLAY_VALUE using QT_QPA_PLATFORM=$QT_PLATFORM_VALUE; pid $(cat viewer.pid)"
