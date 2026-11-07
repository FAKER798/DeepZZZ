#!/usr/bin/env bash
set -Eeuo pipefail

DEMO_DIR="${DEMO_DIR:-/home/z/pose_demo}"
LOG_DIR="$DEMO_DIR/logs"
CAMERA_DEVICE="${CAMERA_DEVICE:-/dev/video20}"
CAMERA_WIDTH="${CAMERA_WIDTH:-640}"
CAMERA_HEIGHT="${CAMERA_HEIGHT:-480}"
CAMERA_FPS="${CAMERA_FPS:-30.0}"
CAMERA_PIXEL_FORMAT="${CAMERA_PIXEL_FORMAT:-yuyv2rgb}"
RESULT_TOPIC="${RESULT_TOPIC:-/result_img}"
IMAGE_TOPIC="${IMAGE_TOPIC:-/image_raw}"
POSE_TOPIC="${POSE_TOPIC:-/inference_pose_result}"

mkdir -p "$LOG_DIR"
cd "$DEMO_DIR"

if [ -x "$DEMO_DIR/stop_pose_demo.sh" ]; then
  "$DEMO_DIR/stop_pose_demo.sh" >/dev/null 2>&1 || true
fi

if [ ! -f "$HOME/.brdk_models/pose/yolov8n-pose.q.onnx" ]; then
  echo "Missing model: $HOME/.brdk_models/pose/yolov8n-pose.q.onnx" >&2
  exit 1
fi

COMMON_ENV=(
  "ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-0}"
  "RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION:-rmw_fastrtps_cpp}"
)

GUI_ENV=(
  "DISPLAY=${DISPLAY:-:0}"
  "QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-xcb}"
  "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/1000}"
  "DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/1000/bus}"
)

nohup env "${COMMON_ENV[@]}" \
  CAMERA_DEVICE="$CAMERA_DEVICE" \
  CAMERA_WIDTH="$CAMERA_WIDTH" \
  CAMERA_HEIGHT="$CAMERA_HEIGHT" \
  CAMERA_FPS="$CAMERA_FPS" \
  CAMERA_PIXEL_FORMAT="$CAMERA_PIXEL_FORMAT" \
  bash -lc '
    source /opt/ros/humble/setup.bash
    source /opt/bros/humble/setup.bash
    exec ros2 run jobot_usb_cam jobot_usb_cam_node_exe --ros-args \
      -p video_device:="$CAMERA_DEVICE" \
      -p framerate:="$CAMERA_FPS" \
      -p io_method:=mmap \
      -p frame_id:=camera \
      -p pixel_format:="$CAMERA_PIXEL_FORMAT" \
      -p image_width:="$CAMERA_WIDTH" \
      -p image_height:="$CAMERA_HEIGHT" \
      -p camera_name:=pose_camera \
      -p camera_info_url:=package://jobot_usb_cam/config/camera_info.yaml \
      -r image_raw:=/image_raw \
      -r image_raw/compressed:=/image_raw/compressed \
      -r camera_info:=/camera_info
  ' >"$LOG_DIR/camera.log" 2>&1 &
echo $! > "$DEMO_DIR/camera.pid"

sleep 2

nohup env "${COMMON_ENV[@]}" \
  IMAGE_TOPIC="$IMAGE_TOPIC" RESULT_TOPIC="$RESULT_TOPIC" POSE_TOPIC="$POSE_TOPIC" \
  bash -lc '
    source /opt/ros/humble/setup.bash
    source /opt/bros/humble/setup.bash
    exec ros2 launch jobot_infer_py infer_video.launch.py \
      config_path:=config/pose/yolov8_pose.yaml \
      sub_image_topic:="$IMAGE_TOPIC" \
      publish_result_img:=false \
      result_img_topic:="$RESULT_TOPIC" \
      result_topic:="$POSE_TOPIC"
  ' >"$LOG_DIR/infer.log" 2>&1 &
echo $! > "$DEMO_DIR/infer.pid"

sleep 2

nohup env "${COMMON_ENV[@]}" "${GUI_ENV[@]}" RESULT_TOPIC="$RESULT_TOPIC" \
  IMAGE_TOPIC="$IMAGE_TOPIC" POSE_TOPIC="$POSE_TOPIC" \
  bash -lc '
    source /opt/ros/humble/setup.bash
    source /opt/bros/humble/setup.bash
    exec python3 /home/z/pose_demo/pose_live_viewer.py --ros-args \
      -p image_topic:="$IMAGE_TOPIC" \
      -p pose_topic:="$POSE_TOPIC" \
      -p publish_topic:="$RESULT_TOPIC" \
      -p publish_result_img:=false
  ' >"$LOG_DIR/viewer.log" 2>&1 &
echo $! > "$DEMO_DIR/viewer.pid"

echo "Started YOLOv8n-Pose quantized demo."
echo "Camera: $CAMERA_DEVICE ${CAMERA_WIDTH}x${CAMERA_HEIGHT}@${CAMERA_FPS} $CAMERA_PIXEL_FORMAT"
echo "Input topic: $IMAGE_TOPIC"
echo "Result image topic: $RESULT_TOPIC"
echo "Pose result topic: $POSE_TOPIC"
echo "Logs: $LOG_DIR"
