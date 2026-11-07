from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

import cv2
import numpy as np


FrameCallback = Callable[[np.ndarray, dict], None]
CAMERA_CAPTURE_FPS = 30


class CameraPipeline:
    def __init__(
        self,
        log_dir: Path,
        frame_callback: FrameCallback,
        device: str = "auto",
        preview_size: str = "1280x720",
        preview_fps: int = 30,
        analysis_width: int = 416,
        analysis_fps: float = 5.0,
    ) -> None:
        self.log_dir = log_dir
        self.frame_callback = frame_callback
        self.device = device
        self.preview_size = preview_size
        self.preview_fps = preview_fps
        self.analysis_width = analysis_width
        self.analysis_fps = analysis_fps
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._processor_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._running = False
        self._error: str | None = None
        self._device_path: str | None = None
        self._frames = 0
        self._preview_frames = 0
        self._preview_times: deque[float] = deque(maxlen=60)
        self._preview_jpeg: bytes | None = None
        self._preview_updated_at = 0.0
        self._source_jpeg: bytes | None = None
        self._source_captured_at = 0.0
        self._source_sequence = 0
        self._source_frames = 0
        self._started_at = 0.0
        self._generation = 0

    def start(self) -> dict:
        with self._lock:
            if self._running:
                return self.status()
            self.log_dir.mkdir(parents=True, exist_ok=True)
            try:
                device = find_camera_device(self.device)
                if self.analysis_fps <= 0:
                    raise ValueError("analysis_fps must be greater than zero")
                process = subprocess.Popen(
                    self._ffmpeg_command(device),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                )
            except Exception as exc:
                self._running = False
                self._error = str(exc)
                raise
            self._process = process
            self._generation += 1
            generation = self._generation
            self._running = True
            self._error = None
            self._device_path = device
            self._frames = 0
            self._preview_frames = 0
            self._preview_times.clear()
            self._preview_jpeg = None
            self._preview_updated_at = 0.0
            self._source_jpeg = None
            self._source_captured_at = 0.0
            self._source_sequence = 0
            self._source_frames = 0
            self._started_at = time.time()
            self._stderr_thread = threading.Thread(
                target=self._read_stderr,
                args=(process, "ffmpeg-camera.log", "camera"),
                daemon=True,
            )
            self._reader_thread = threading.Thread(
                target=self._read_source_pipe,
                args=(process, generation),
                daemon=True,
            )
            self._processor_thread = threading.Thread(
                target=self._process_latest_frames,
                args=(generation,),
                daemon=True,
            )
            self._reader_thread.start()
            self._processor_thread.start()
            self._stderr_thread.start()
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            process = self._process
            self._process = None
            self._running = False
            self._generation += 1
        self._stop_process(process)
        return self.status()

    def status(self) -> dict:
        with self._lock:
            alive = self._process is not None and self._process.poll() is None
            now = time.time()
            preview_actual_fps = 0.0
            if len(self._preview_times) >= 2:
                elapsed = self._preview_times[-1] - self._preview_times[0]
                if elapsed > 0:
                    preview_actual_fps = round((len(self._preview_times) - 1) / elapsed, 2)
            return {
                "running": self._running and alive,
                "analysis_running": alive,
                "device": self._device_path,
                "preview_size": self.preview_size,
                "preview_fps": self.preview_fps,
                "analysis_width": self.analysis_width,
                "analysis_fps": self.analysis_fps,
                "preview_actual_fps": preview_actual_fps,
                "preview_updated_at": self._preview_updated_at,
                "preview_frame_age_s": round(now - self._preview_updated_at, 3) if self._preview_updated_at else None,
                "preview_frames": self._preview_frames,
                "source_frames": self._source_frames,
                "capture_format": "mjpeg",
                "capture_fps": CAMERA_CAPTURE_FPS,
                "frames": self._frames,
                "uptime_s": round(time.time() - self._started_at, 1) if self._started_at else 0,
                "error": self._error,
                "preview_ready": self._preview_jpeg is not None,
            }

    def _ffmpeg_command(self, device: str) -> list[str]:
        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "v4l2",
            "-input_format",
            "mjpeg",
            "-video_size",
            self.preview_size,
            "-framerate",
            str(CAMERA_CAPTURE_FPS),
            "-i",
            device,
            "-an",
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-flush_packets",
            "1",
            "-f",
            "image2pipe",
            "pipe:1",
        ]

    def _read_source_pipe(self, process: subprocess.Popen, generation: int) -> None:
        if process is None or process.stdout is None:
            return
        buffer = bytearray()
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                break
            buffer.extend(chunk)
            while True:
                start = buffer.find(b"\xff\xd8")
                if start < 0:
                    if len(buffer) > 1:
                        del buffer[:-1]
                    break
                end = buffer.find(b"\xff\xd9", start + 2)
                if end < 0:
                    if start:
                        del buffer[:start]
                    break
                jpeg = bytes(buffer[start : end + 2])
                del buffer[: end + 2]
                now = time.time()
                with self._lock:
                    if generation != self._generation:
                        return
                    self._source_jpeg = jpeg
                    self._source_captured_at = now
                    self._source_sequence += 1
                    self._source_frames += 1
                    self._preview_jpeg = jpeg
                    self._preview_updated_at = now
                    self._preview_frames += 1
                    self._preview_times.append(now)
        with self._lock:
            if self._running and generation == self._generation:
                self._error = "ffmpeg camera pipe ended"

    def _process_latest_frames(self, generation: int) -> None:
        interval = 1.0 / min(self.analysis_fps, float(self.preview_fps))
        next_frame_at = time.monotonic()
        processed_sequence = -1
        while True:
            with self._lock:
                if not self._running or generation != self._generation:
                    return
                jpeg = self._source_jpeg
                captured_at = self._source_captured_at
                sequence = self._source_sequence
            if jpeg is not None and sequence != processed_sequence:
                self._process_source_jpeg(jpeg, captured_at)
                processed_sequence = sequence
            next_frame_at += interval
            now = time.monotonic()
            if next_frame_at <= now:
                next_frame_at = now + interval
            remaining = next_frame_at - now
            if remaining > 0:
                time.sleep(remaining)

    def _process_source_jpeg(self, jpeg: bytes, captured_at: float) -> bool:
        frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return False
        width, height = self._analysis_size()
        lowres = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        self.frame_callback(lowres, {"source": "latest-mjpeg", "captured_at": captured_at})
        with self._lock:
            self._frames += 1
        return True

    def latest_preview_jpeg(self, max_age_s: float = 2.0) -> bytes | None:
        with self._lock:
            if self._preview_jpeg is None or time.time() - self._preview_updated_at > max_age_s:
                return None
            return self._preview_jpeg

    def _analysis_size(self) -> tuple[int, int]:
        try:
            pw, ph = (int(x) for x in self.preview_size.lower().split("x"))
        except ValueError:
            return self.analysis_width, 180
        width = self.analysis_width
        height = int(ph * width / pw)
        if height % 2:
            height -= 1
        return width, height

    def _read_stderr(self, process: subprocess.Popen | None, filename: str, source: str) -> None:
        if process is None or process.stderr is None:
            return
        log_path = self.log_dir / filename
        last_text = ""
        with log_path.open("ab") as handle:
            while True:
                chunk = process.stderr.readline()
                if not chunk:
                    break
                handle.write(chunk)
                handle.flush()
                text = chunk.decode("utf-8", errors="replace").strip()
                if text:
                    last_text = text
        return_code = process.poll()
        if return_code not in (None, 0):
            with self._lock:
                if self._running:
                    detail = last_text[-440:] if last_text else "no stderr detail"
                    self._error = f"{source} ffmpeg exited ({return_code}): {detail}"

    @staticmethod
    def _stop_process(process: subprocess.Popen | None) -> None:
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass


def find_camera_device(device: str) -> str:
    if device and device != "auto":
        if Path(device).exists():
            return device
        raise FileNotFoundError(f"camera device does not exist: {device}")

    candidates: list[Path] = []
    for pattern in (
        "/dev/v4l/by-id/*video-index0",
        "/dev/v4l/by-path/*usb*video-index0",
        "/dev/video*",
    ):
        candidates.extend(sorted(Path("/").glob(pattern.removeprefix("/"))))

    for path in candidates:
        name = str(path)
        if any(token in name.lower() for token in ("usb", "camera", "hdmi")):
            return name

    for path in candidates:
        return str(path)
    raise FileNotFoundError("no camera device found")
