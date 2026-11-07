from __future__ import annotations

from collections import deque
import os
import threading
import time
from typing import Any

import cv2
import numpy as np

from ai.low_light import enhance_low_light, should_enhance_low_light
from ai.runtime import CPU_PROVIDER, provider_label
from ai.pose import YoloPoseEngine
from devices.day_night_sensor import day_night_status


PENDING_FRAME_LIMIT = 1


class VisionWorker:
    def __init__(
        self,
        model_path: str | None = None,
        enabled: bool = False,
        provider: str = CPU_PROVIDER,
        provider_fallback: bool = True,
        low_light_mode: str = "off",
        low_light_gamma: float = 1.35,
        low_light_clahe_clip: float = 2.0,
        low_light_desaturate: bool = True,
    ) -> None:
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._decode_pending: deque[tuple] = deque(maxlen=1)
        self._decode_event = threading.Event()
        self._decode_thread: threading.Thread | None = None
        self._pending: deque[tuple[np.ndarray, dict[str, Any]]] = deque(maxlen=PENDING_FRAME_LIMIT)
        self._latest: dict[str, Any] | None = None
        self._latest_raw_jpeg: bytes | None = None
        self._latest_jpeg: bytes | None = None
        self._running = False
        self._submitted = 0
        self._overwritten = 0
        self._processed = 0
        self._processed_at: deque[float] = deque(maxlen=120)
        self._profile: dict[str, Any] | None = None
        self._profile_history: deque[dict[str, float]] = deque(maxlen=120)
        self._model_path = model_path
        self._model_enabled = enabled
        self._provider = provider
        self._provider_fallback = provider_fallback
        self._low_light_mode = low_light_mode
        self._low_light_gamma = low_light_gamma
        self._low_light_clahe_clip = low_light_clahe_clip
        self._low_light_desaturate = low_light_desaturate
        self._engine: YoloPoseEngine | None = None
        self._error: str | None = None
        self._active_provider: str | None = None
        self._active_providers: list[str] = []
        # Reasons inference is paused for (e.g. "assistant" while the local LLM answers).
        # Frames are dropped while paused; the last result is kept as is.
        self._pause_reasons: set[str] = set()
        self._paused_at: float | None = None

    def pause(self, reason: str = "manual") -> None:
        with self._lock:
            if not self._pause_reasons:
                self._paused_at = time.time()
            self._pause_reasons.add(reason)
            self._pending.clear()

    def resume(self, reason: str = "manual") -> None:
        with self._lock:
            self._pause_reasons.discard(reason)
            if not self._pause_reasons:
                self._paused_at = None

    @property
    def paused(self) -> bool:
        return bool(self._pause_reasons)

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._stop.clear()
            self._running = True
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            self._decode_thread = threading.Thread(target=self._decode_loop, daemon=True)
            self._decode_thread.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._stop.set()
            self._event.set()
            thread = self._thread
            self._thread = None
            decode_thread = self._decode_thread
            self._decode_thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        if decode_thread is not None and decode_thread.is_alive():
            decode_thread.join(timeout=2)

    def submit_frame(self, frame: np.ndarray, meta: dict[str, Any]) -> None:
        with self._lock:
            if self._pause_reasons:
                return
            if len(self._pending) >= PENDING_FRAME_LIMIT:
                self._overwritten += 1
            meta = {**meta, "submitted_at": time.time()}
            self._pending.append((frame.copy(), meta))
            self._submitted += 1
            self._event.set()

    def latest_result(self) -> dict[str, Any] | None:
        with self._lock:
            return self._latest

    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._latest_jpeg

    def latest_raw_jpeg(self) -> bytes | None:
        with self._lock:
            return self._latest_raw_jpeg

    def status(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            return {
                "running": self._running,
                "engine": self._engine_name(),
                "processed": self._processed,
                "submitted": self._submitted,
                "overwritten": self._overwritten,
                "pending": len(self._pending),
                "pending_limit": PENDING_FRAME_LIMIT,
                "actual_fps": recent_rate(self._processed_at, now=now),
                "latest": self._latest,
                "profile": self._profile,
                "profile_stats": summarize_profiles(self._profile_history),
                "model_enabled": self._model_enabled,
                "paused": bool(self._pause_reasons),
                "pause_reasons": sorted(self._pause_reasons),
                "paused_at": self._paused_at,
                "model_path": self._model_path,
                "provider": self._active_provider or self._provider,
                "requested_provider": self._provider,
                "providers": self._active_providers,
                "provider_fallback": self._provider_fallback,
                "low_light": {
                    "mode": self._low_light_mode,
                    "gamma": self._low_light_gamma,
                    "clahe_clip": self._low_light_clahe_clip,
                    "desaturate": self._low_light_desaturate,
                },
                "error": self._error,
            }

    def _run(self) -> None:
        os.sched_setaffinity(0, {0, 1, 2, 3})  # 推理线程绑定 CPU0-3，ONNX 线程池继承
        while not self._stop.is_set():
            with self._lock:
                packet = self._pending.popleft() if self._pending else None
            if packet is None:
                self._event.wait(timeout=0.5)
                self._event.clear()
                continue
            frame, meta = packet
            if self._pause_reasons:
                continue  # paused: free CPU0-3 (e.g. for the local LLM), drop the frame
            if not self._model_enabled:
                result, preview = self._sample(frame, meta)
                with self._lock:
                    self._latest = result
                continue
            try:
                if self._engine is None:
                    if not self._model_path:
                        raise RuntimeError("pose model is enabled but model path is empty")
                    self._engine = YoloPoseEngine(
                        self._model_path,
                        provider=self._provider,
                        provider_fallback=self._provider_fallback,
                    )
                    self._active_provider = self._engine.provider
                    self._active_providers = self._engine.session.get_providers()
                day_night = day_night_status()
                day_night_state = str(day_night.get("state") or "unknown")
                enhance, brightness, low_light_mode = should_enhance_low_light(
                    frame,
                    self._low_light_mode,
                    day_night_state,
                )
                inference_frame = (
                    enhance_low_light(
                        frame,
                        gamma=self._low_light_gamma,
                        clahe_clip_limit=self._low_light_clahe_clip,
                        desaturate=self._low_light_desaturate,
                    )
                    if enhance
                    else frame
                )
                result, detections = self._engine.infer(inference_frame)
                self._decode_pending.append(
                    (
                        inference_frame,
                        result,
                        detections,
                        meta,
                        {
                            "mode": low_light_mode,
                            "active": enhance,
                            "brightness": round(brightness, 2),
                            "day_night": day_night_state,
                            "gamma": self._low_light_gamma,
                            "clahe_clip": self._low_light_clahe_clip,
                            "desaturate": self._low_light_desaturate,
                        },
                    )
                )
                self._decode_event.set()
            except Exception as exc:
                self._error = str(exc)
                result, preview = self._sample(frame, meta)
                result["error"] = str(exc)
                with self._lock:
                    self._latest = result

    def _decode_loop(self) -> None:
        os.sched_setaffinity(0, {0, 1, 2, 3})  # 解码线程绑 0-3，避免与 ffmpeg(4-7) 抢核
        while not self._stop.is_set():
            with self._lock:
                item = self._decode_pending.popleft() if self._decode_pending else None
            if item is None:
                self._decode_event.wait(timeout=0.5)
                self._decode_event.clear()
                continue
            inference_frame, result, detections, meta, low_light = item
            decode_started = time.perf_counter()
            try:
                annotated, draw_ms = self._engine.draw(inference_frame, detections)
                result["draw_ms"] = round(draw_ms, 2)
                result["captured_at"] = meta.get("captured_at")
                result["low_light"] = low_light
                self._active_provider = result.get("provider")
                self._active_providers = result.get("providers") or self._active_providers
                self._error = None
            except Exception as exc:
                self._error = str(exc)
                annotated = inference_frame
            raw_jpeg_started = time.perf_counter()
            raw_ok, raw_encoded = cv2.imencode(".jpg", inference_frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            raw_jpeg_ms = (time.perf_counter() - raw_jpeg_started) * 1000.0
            preview_jpeg_started = time.perf_counter()
            ok, encoded = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 78])
            preview_jpeg_ms = (time.perf_counter() - preview_jpeg_started) * 1000.0
            with self._lock:
                decode_ms = (time.perf_counter() - decode_started) * 1000.0
                profile = {
                    "decode_ms": round(decode_ms, 2),
                    "model_elapsed_ms": result.get("elapsed_ms"),
                    "raw_jpeg_ms": round(raw_jpeg_ms, 2),
                    "preview_jpeg_ms": round(preview_jpeg_ms, 2),
                    "total_ms": round(decode_ms, 2),
                    "pending_after_process": bool(self._pending),
                    "pending_count_after_process": len(self._pending),
                    "decode_pending_count": len(self._decode_pending),
                }
                self._processed_at.append(time.time())
                self._processed += 1
                self._latest = result
                self._latest_raw_jpeg = raw_encoded.tobytes() if raw_ok else None
                self._latest_jpeg = encoded.tobytes() if ok else None
                self._profile = profile
                self._profile_history.append(
                    {
                        key: float(value)
                        for key, value in profile.items()
                        if isinstance(value, (int, float)) and value is not None
                    }
                )

    def _sample(self, frame: np.ndarray, meta: dict[str, Any]) -> tuple[dict[str, Any], np.ndarray]:
        started = time.perf_counter()
        preview = frame.copy()
        text = f"sample {frame.shape[1]}x{frame.shape[0]} @ low fps  no model"
        cv2.rectangle(preview, (0, 0), (preview.shape[1], 30), (20, 24, 28), -1)
        cv2.putText(preview, text, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (245, 245, 240), 1, cv2.LINE_AA)

        result = {
            "engine": "lowres_sampler",
            "frame_size": [int(frame.shape[1]), int(frame.shape[0])],
            "captured_at": meta.get("captured_at"),
            "updated_at": time.time(),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "model_active": False,
        }
        return result, preview

    def _engine_name(self) -> str:
        if not self._model_enabled:
            return "lowres_sampler"
        provider = self._active_provider or self._provider
        return f"yolov8n_pose_{provider_label(provider)}"


def recent_rate(timestamps: deque[float], now: float | None = None, window_s: float = 5.0) -> float | None:
    now = time.time() if now is None else now
    recent = [value for value in timestamps if now - value <= window_s]
    if len(recent) < 2:
        return None
    span = recent[-1] - recent[0]
    if span <= 0:
        return None
    return round((len(recent) - 1) / span, 2)


def summarize_profiles(profiles: deque[dict[str, float]]) -> dict[str, dict[str, float | None]]:
    keys = ("queued_ms", "infer_ms", "model_elapsed_ms", "raw_jpeg_ms", "preview_jpeg_ms", "lock_ms", "total_ms")
    return {key: summarize_profile_values([profile[key] for profile in profiles if key in profile]) for key in keys}


def summarize_profile_values(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"avg": None, "p95": None, "max": None}
    sorted_values = sorted(values)
    p95_index = min(len(sorted_values) - 1, max(0, round((len(sorted_values) - 1) * 0.95)))
    return {
        "avg": round(sum(values) / len(values), 2),
        "p95": round(sorted_values[p95_index], 2),
        "max": round(max(values), 2),
    }
