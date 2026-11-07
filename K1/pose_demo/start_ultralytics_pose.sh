#!/usr/bin/env bash
set -Eeuo pipefail

DEMO_DIR="${DEMO_DIR:-/home/z/pose_demo}"
LOG_DIR="$DEMO_DIR/logs"
MODEL="${MODEL:-/home/z/.brdk_models/pose/yolov8n-pose-320.onnx}"
SOURCE="${SOURCE:-/dev/video20}"
WIDTH="${WIDTH:-1280}"
HEIGHT="${HEIGHT:-720}"
FPS="${FPS:-30}"
IMGSZ="${IMGSZ:-0}"
INFER_WIDTH="${INFER_WIDTH:-320}"
INFER_HEIGHT="${INFER_HEIGHT:-240}"
CONF="${CONF:-0.5}"
IOU="${IOU:-0.7}"
WINDOW="${WINDOW:-YOLO Pose Ultralytics Direct}"

mkdir -p "$LOG_DIR"
cd "$DEMO_DIR"

if [ -x "$DEMO_DIR/stop_pose_demo.sh" ]; then
  "$DEMO_DIR/stop_pose_demo.sh" >/dev/null 2>&1 || true
fi

nohup env \
  DISPLAY="${DISPLAY:-:0}" \
  QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}" \
  XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}" \
  DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/1000/bus}" \
  MODEL="$MODEL" \
  SOURCE="$SOURCE" \
  WIDTH="$WIDTH" \
  HEIGHT="$HEIGHT" \
  FPS="$FPS" \
  IMGSZ="$IMGSZ" \
  INFER_WIDTH="$INFER_WIDTH" \
  INFER_HEIGHT="$INFER_HEIGHT" \
  CONF="$CONF" \
  IOU="$IOU" \
  WINDOW="$WINDOW" \
  bash -lc '
    set +u
    source /opt/ros/humble/setup.bash
    source /opt/bros/humble/setup.bash
    set -u
    exec python3 /home/z/pose_demo/pose_ultralytics_spacemit.py \
      --model "$MODEL" \
      --source "$SOURCE" \
      --width "$WIDTH" \
      --height "$HEIGHT" \
      --fps "$FPS" \
      --imgsz "$IMGSZ" \
      --infer-width "$INFER_WIDTH" \
      --infer-height "$INFER_HEIGHT" \
      --conf "$CONF" \
      --iou "$IOU" \
      --window "$WINDOW"
  ' >"$LOG_DIR/ultralytics_pose.log" 2>&1 &

echo "$!" > "$DEMO_DIR/ultralytics_pose.pid"
echo "Started Ultralytics-aligned YOLO pose on SpaceMIT, pid $(cat "$DEMO_DIR/ultralytics_pose.pid")"
echo "Log: $LOG_DIR/ultralytics_pose.log"
