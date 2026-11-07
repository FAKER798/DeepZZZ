#!/usr/bin/env bash
set -Eeuo pipefail

set +u
source /opt/ros/humble/setup.bash
source /opt/bros/humble/setup.bash
set -u

export DISPLAY="${DISPLAY:-:0}"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/1000/bus}"
unset WAYLAND_DISPLAY

echo "DISPLAY=$DISPLAY"
echo "QT_QPA_PLATFORM=$QT_QPA_PLATFORM"
echo "XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"
python3 - <<'PY'
import cv2
print("cv2 version", cv2.__version__)
print("has gui?", "GUI:" in cv2.getBuildInformation())
for line in cv2.getBuildInformation().splitlines():
    if "GUI:" in line or "QT:" in line or "GTK" in line:
        print(line)
PY

exec python3 /home/z/pose_demo/pose_result_viewer.py --ros-args -p topic:=/result_img
