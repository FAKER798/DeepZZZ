#!/usr/bin/env python3
import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage


class PoseResultViewer(Node):
    def __init__(self):
        super().__init__("pose_result_viewer")
        self.declare_parameter("topic", "/result_img")
        self.declare_parameter("window", "YOLOv8n-Pose Quantized")
        self.declare_parameter("width", 960)
        self.declare_parameter("height", 720)

        self.topic = self.get_parameter("topic").value
        self.window = self.get_parameter("window").value
        self.width = int(self.get_parameter("width").value)
        self.height = int(self.get_parameter("height").value)
        self.frame = self._waiting_frame()

        cv2.namedWindow(self.window, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.window, self.width, self.height)
        cv2.moveWindow(self.window, 0, 0)

        self.create_subscription(CompressedImage, self.topic, self._on_image, 5)
        self.create_timer(1.0 / 30.0, self._draw)
        self.get_logger().info(f"Viewing compressed image topic: {self.topic}")

    def _waiting_frame(self):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(
            frame,
            "Waiting for YOLOv8n-Pose result...",
            (35, 230),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 220, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            self.topic,
            (35, 270),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (220, 220, 220),
            1,
            cv2.LINE_AA,
        )
        return frame

    def _on_image(self, msg):
        data = np.frombuffer(msg.data, dtype=np.uint8)
        frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if frame is not None:
            self.frame = frame

    def _draw(self):
        cv2.imshow(self.window, self.frame)
        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord("q")):
            rclpy.shutdown()


def main():
    rclpy.init()
    node = PoseResultViewer()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
