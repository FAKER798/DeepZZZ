from __future__ import annotations

import argparse
import threading
import time
from typing import Any

from ai.vision_ai import VisionWorker
from devices.camera import CameraPipeline
from utils.paths import LOG_DIR


class VideoPipeline:
    def __init__(self, args: argparse.Namespace) -> None:
        self.vision = VisionWorker(
            model_path=args.pose_model,
            enabled=args.enable_pose,
            provider=args.pose_provider,
            provider_fallback=args.pose_provider_fallback,
            low_light_mode=args.low_light_mode,
            low_light_gamma=args.low_light_gamma,
            low_light_clahe_clip=args.low_light_clahe_clip,
            low_light_desaturate=args.low_light_desaturate,
        )
        self.camera = CameraPipeline(
            log_dir=LOG_DIR,
            frame_callback=self.vision.submit_frame,
            device=args.device,
            preview_size=args.preview_size,
            preview_fps=args.preview_fps,
            analysis_width=args.analysis_width,
            analysis_fps=args.analysis_fps,
        )
        self.start_camera = bool(args.start_camera)
        self._stop = threading.Event()
        self._watchdog_thread: threading.Thread | None = None
        self._camera_restarts = 0
        self._last_camera_restart_at: float | None = None
        self._last_camera_restart_reason: str | None = None
        self._camera_recovering = False

    def start(self) -> None:
        self._stop.clear()
        self.vision.start()
        if self.start_camera:
            self._start_camera()
            self._watchdog_thread = threading.Thread(
                target=self._camera_watchdog,
                name="camera-watchdog",
                daemon=True,
            )
            self._watchdog_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.camera.stop()
        watchdog = self._watchdog_thread
        self._watchdog_thread = None
        if watchdog is not None and watchdog is not threading.current_thread():
            watchdog.join(timeout=2)
        self.vision.stop()

    def status(self) -> dict[str, Any]:
        return {
            "camera": self.camera.status(),
            "vision": self.vision.status(),
            "camera_watchdog": {
                "running": self._watchdog_thread is not None and self._watchdog_thread.is_alive(),
                "recovering": self._camera_recovering,
                "restarts": self._camera_restarts,
                "last_restart_at": self._last_camera_restart_at,
                "last_restart_reason": self._last_camera_restart_reason,
            },
        }

    def _start_camera(self) -> bool:
        try:
            self.camera.start()
            return True
        except Exception as exc:
            print(f"camera start failed: {exc}", flush=True)
            return False

    def _camera_watchdog(self) -> None:
        retry_delay = CAMERA_RETRY_MIN_SECONDS
        while not self._stop.wait(CAMERA_WATCHDOG_INTERVAL_SECONDS):
            status = self.camera.status()
            reason = camera_restart_reason(status)
            if reason is None:
                retry_delay = CAMERA_RETRY_MIN_SECONDS
                continue
            self._camera_recovering = True
            self._last_camera_restart_reason = reason
            print(f"camera watchdog restarting capture: {reason}", flush=True)
            self.camera.stop()
            if self._stop.wait(retry_delay):
                break
            if self._start_camera():
                self._camera_restarts += 1
                self._last_camera_restart_at = time.time()
                retry_delay = CAMERA_RETRY_MIN_SECONDS
            else:
                retry_delay = min(CAMERA_RETRY_MAX_SECONDS, retry_delay * 2.0)
            self._camera_recovering = False
        self._camera_recovering = False


CAMERA_WATCHDOG_INTERVAL_SECONDS = 1.0
CAMERA_STARTUP_GRACE_SECONDS = 5.0
CAMERA_STALE_SECONDS = 3.0
CAMERA_RETRY_MIN_SECONDS = 0.5
CAMERA_RETRY_MAX_SECONDS = 5.0


def camera_restart_reason(status: dict[str, Any]) -> str | None:
    if status.get("uptime_s", 0.0) < CAMERA_STARTUP_GRACE_SECONDS:
        return None
    if not status.get("running"):
        return "capture process stopped"
    frame_age = status.get("preview_frame_age_s")
    if frame_age is None:
        return "no frame received after startup"
    if float(frame_age) > CAMERA_STALE_SECONDS:
        return f"latest frame is {float(frame_age):.1f}s old"
    return None
