#!/usr/bin/env python3
import math
import time

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CompressedImage
from std_msgs.msg import Header
from jobot_interfaces.msg import DetectionPoseResultArray

try:
    from cv_bridge import CvBridge
except Exception:
    from jobot_perception_utils_py.cv_bridge import CvBridge


KPT_NAMES = [
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
]

PARTS = {
    "head": {
        "color": (0, 220, 255),
        "edges": [(0, 1), (0, 2), (1, 3), (2, 4)],
    },
    "torso": {
        "color": (80, 220, 80),
        "edges": [(5, 6), (5, 11), (6, 12), (11, 12)],
    },
    "left_arm": {
        "color": (255, 160, 40),
        "edges": [(5, 7), (7, 9)],
    },
    "right_arm": {
        "color": (40, 150, 255),
        "edges": [(6, 8), (8, 10)],
    },
    "left_leg": {
        "color": (210, 80, 255),
        "edges": [(11, 13), (13, 15)],
    },
    "right_leg": {
        "color": (60, 230, 230),
        "edges": [(12, 14), (14, 16)],
    },
}

KPT_COLORS = {}
for part in PARTS.values():
    for a, b in part["edges"]:
        KPT_COLORS.setdefault(a, part["color"])
        KPT_COLORS.setdefault(b, part["color"])


class PoseLiveViewer(Node):
    def __init__(self):
        super().__init__("pose_live_viewer")
        self.declare_parameter("image_topic", "/image_raw")
        self.declare_parameter("pose_topic", "/inference_pose_result")
        self.declare_parameter("publish_topic", "/result_img")
        self.declare_parameter("window", "YOLOv8n-Pose Quantized")
        self.declare_parameter("width", 640)
        self.declare_parameter("height", 480)
        self.declare_parameter("smooth_alpha", 0.45)
        self.declare_parameter("pose_ttl_sec", 0.45)
        self.declare_parameter("display_fps", 15.0)
        self.declare_parameter("image_max_fps", 15.0)
        self.declare_parameter("publish_result_img", False)

        self.image_topic = self.get_parameter("image_topic").value
        self.pose_topic = self.get_parameter("pose_topic").value
        self.publish_topic = self.get_parameter("publish_topic").value
        self.window = self.get_parameter("window").value
        self.width = int(self.get_parameter("width").value)
        self.height = int(self.get_parameter("height").value)
        self.alpha = float(self.get_parameter("smooth_alpha").value)
        self.pose_ttl = float(self.get_parameter("pose_ttl_sec").value)
        self.display_fps = max(5.0, float(self.get_parameter("display_fps").value))
        self.image_max_fps = max(5.0, float(self.get_parameter("image_max_fps").value))
        self.publish_result_img = bool(self.get_parameter("publish_result_img").value)

        self.bridge = CvBridge()
        self.latest_frame = self._waiting_frame()
        self.latest_pose_time = 0.0
        self.last_image_time = 0.0
        self.tracks = []
        self.fps_times = []

        self.create_subscription(Image, self.image_topic, self._on_image, 3)
        self.create_subscription(DetectionPoseResultArray, self.pose_topic, self._on_pose, 10)
        if self.publish_result_img:
            self.result_pub = self.create_publisher(CompressedImage, self.publish_topic, 3)
        else:
            self.result_pub = None

        cv2.namedWindow(self.window, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.window, self.width, self.height)
        cv2.moveWindow(self.window, 0, 0)
        self.create_timer(1.0 / self.display_fps, self._draw)
        self.get_logger().info(
            f"Live view image={self.image_topic}, pose={self.pose_topic}, "
            f"publish_result_img={self.publish_result_img}"
        )

    def _waiting_frame(self):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(frame, "Waiting for camera and YOLOv8n-Pose...", (35, 230),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 220, 255), 2, cv2.LINE_AA)
        return frame

    def _on_image(self, msg):
        now = time.monotonic()
        if now - self.last_image_time < 1.0 / self.image_max_fps:
            return
        self.last_image_time = now
        try:
            self.latest_frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        except Exception as exc:
            self.get_logger().warn(f"Failed to convert image: {exc}")

    def _on_pose(self, msg):
        detections = [self._msg_to_det(det) for det in msg.results]
        now = time.monotonic()
        self._update_tracks(detections, now)
        self.latest_pose_time = now

    def _msg_to_det(self, det):
        kpts = np.full((17, 3), np.nan, dtype=np.float32)
        for idx, x, y, conf in zip(det.keypoint_ids, det.keypoint_xs, det.keypoint_ys, det.keypoint_confs):
            if 0 <= idx < 17:
                kpts[idx] = (float(x), float(y), float(conf))
        box = np.array([det.x_min, det.y_min, det.x_min + det.width, det.y_min + det.height], dtype=np.float32)
        return {
            "box": box,
            "kpts": kpts,
            "conf": float(det.conf),
            "label": det.label or "person",
        }

    def _update_tracks(self, detections, now):
        unmatched = set(range(len(self.tracks)))
        new_tracks = []
        for det in detections:
            det_center = self._center(det["box"])
            best_idx = None
            best_dist = 1e9
            for idx in list(unmatched):
                track = self.tracks[idx]
                dist = self._dist(det_center, self._center(track["box"]))
                if dist < best_dist:
                    best_dist = dist
                    best_idx = idx
            if best_idx is not None and best_dist < 140:
                track = self.tracks[best_idx]
                unmatched.remove(best_idx)
                track["box"] = self._ema(track["box"], det["box"])
                track["kpts"] = self._ema_kpts(track["kpts"], det["kpts"])
                track["conf"] = det["conf"]
                track["label"] = det["label"]
                track["last_seen"] = now
                new_tracks.append(track)
            else:
                det["last_seen"] = now
                new_tracks.append(det)

        for idx in unmatched:
            track = self.tracks[idx]
            if now - track["last_seen"] < self.pose_ttl:
                new_tracks.append(track)
        self.tracks = new_tracks[:8]

    def _ema(self, old, new):
        return old * (1.0 - self.alpha) + new * self.alpha

    def _ema_kpts(self, old, new):
        out = old.copy()
        for idx in range(17):
            if np.isfinite(new[idx, 0]) and np.isfinite(old[idx, 0]):
                out[idx] = old[idx] * (1.0 - self.alpha) + new[idx] * self.alpha
            elif np.isfinite(new[idx, 0]):
                out[idx] = new[idx]
        return out

    @staticmethod
    def _center(box):
        return ((box[0] + box[2]) * 0.5, (box[1] + box[3]) * 0.5)

    @staticmethod
    def _dist(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def _draw(self):
        frame = self.latest_frame.copy()
        now = time.monotonic()
        if now - self.latest_pose_time <= self.pose_ttl:
            for track in self.tracks:
                self._draw_pose(frame, track)
        self._draw_status(frame, now)
        cv2.imshow(self.window, frame)
        if self.result_pub is not None:
            self._publish_result(frame)
        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord("q")):
            rclpy.shutdown()

    def _draw_pose(self, frame, det):
        box = det["box"].astype(int)
        cv2.rectangle(frame, (box[0], box[1]), (box[2], box[3]), (240, 240, 240), 2)
        cv2.putText(frame, f"{det['label']} {det['conf']:.2f}", (box[0], max(20, box[1] - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (240, 240, 240), 2, cv2.LINE_AA)

        kpts = det["kpts"]
        for part in PARTS.values():
            color = part["color"]
            for a, b in part["edges"]:
                if self._valid(kpts[a]) and self._valid(kpts[b]):
                    pt_a = (int(kpts[a][0]), int(kpts[a][1]))
                    pt_b = (int(kpts[b][0]), int(kpts[b][1]))
                    cv2.line(frame, pt_a, pt_b, color, 4, cv2.LINE_AA)

        for idx, point in enumerate(kpts):
            if self._valid(point):
                color = KPT_COLORS.get(idx, (255, 255, 255))
                center = (int(point[0]), int(point[1]))
                cv2.circle(frame, center, 5, color, -1, cv2.LINE_AA)
                cv2.circle(frame, center, 6, (20, 20, 20), 1, cv2.LINE_AA)

    @staticmethod
    def _valid(point):
        return np.isfinite(point[0]) and np.isfinite(point[1]) and point[2] > 0.05

    def _draw_status(self, frame, now):
        self.fps_times = [t for t in self.fps_times if now - t < 1.0]
        self.fps_times.append(now)
        fps = len(self.fps_times)
        cv2.rectangle(frame, (8, 8), (214, 38), (0, 0, 0), -1)
        cv2.putText(frame, f"view {fps:02d} fps  people {len(self.tracks)}", (16, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 1, cv2.LINE_AA)

    def _publish_result(self, frame):
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        if not ok:
            return
        msg = CompressedImage()
        msg.header = Header()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.format = "jpeg"
        msg.data = encoded.tobytes()
        self.result_pub.publish(msg)


def main():
    rclpy.init()
    node = PoseLiveViewer()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
