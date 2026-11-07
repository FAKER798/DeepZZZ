from __future__ import annotations

import csv
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np


DEFAULT_MODEL = Path("models/yamnet/yamnet.onnx")
DEFAULT_LABELS = Path("models/yamnet/yamnet_class_map.csv")
CRY_LABELS = {
    "Baby cry, infant cry",
    "Crying, sobbing",
    "Screaming",
}


class YamnetCryDetector:
    def __init__(
        self,
        model_path: str | Path = DEFAULT_MODEL,
        labels_path: str | Path = DEFAULT_LABELS,
        provider: str = "CPUExecutionProvider",
    ) -> None:
        self.model_path = str(model_path)
        self.labels = load_labels(labels_path)
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        self.session = ort.InferenceSession(self.model_path, sess_options=options, providers=[provider])
        self.input_name = self.session.get_inputs()[0].name
        self.output_names = [output.name for output in self.session.get_outputs()]
        self.provider = self.session.get_providers()[0]

    def infer_waveform(self, waveform: np.ndarray, sample_rate: int) -> dict[str, Any]:
        if sample_rate != 16000:
            raise ValueError(f"YAMNet expects 16000 Hz audio, got {sample_rate}")
        if waveform.dtype != np.float32:
            waveform = waveform.astype(np.float32)
        started = time.perf_counter()
        scores, _embeddings, _spectrogram = self.session.run(self.output_names, {self.input_name: waveform})
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        mean_scores = scores.mean(axis=0)
        top_indices = np.argsort(mean_scores)[-10:][::-1]
        top = [
            {
                "label": self.labels[int(index)],
                "score": round(float(mean_scores[int(index)]), 4),
            }
            for index in top_indices
        ]
        cry_indices = [index for index, label in enumerate(self.labels) if label in CRY_LABELS]
        if cry_indices:
            cry_frame_scores = scores[:, cry_indices].max(axis=1)
            top_count = min(3, int(cry_frame_scores.size))
            cry_score = float(np.sort(cry_frame_scores)[-top_count:].mean())
            cry_score_mean = float(cry_frame_scores.mean())
            cry_score_max = float(cry_frame_scores.max())
            cry_score_p90 = float(np.percentile(cry_frame_scores, 90))
        else:
            cry_score = cry_score_mean = cry_score_max = cry_score_p90 = 0.0
        return {
            "engine": "yamnet_onnx",
            "provider": self.provider,
            "elapsed_ms": elapsed_ms,
            "sample_rate": sample_rate,
            "duration_s": round(float(waveform.size) / sample_rate, 3),
            "frames": int(scores.shape[0]),
            "cry_score": round(cry_score, 4),
            "cry_score_mean": round(cry_score_mean, 4),
            "cry_score_max": round(cry_score_max, 4),
            "cry_score_p90": round(cry_score_p90, 4),
            "cry_score_mode": "top3_frame_mean",
            "raw_crying": cry_score >= 0.35,
            "crying": cry_score >= 0.35,
            "top": top,
            "updated_at": time.time(),
        }


class CryStateTracker:
    def __init__(
        self,
        enter_threshold: float = 0.45,
        exit_threshold: float = 0.2,
        enter_count: int = 1,
        exit_count: int = 4,
        hold_seconds: float = 2.0,
    ) -> None:
        self.enter_threshold = float(enter_threshold)
        self.exit_threshold = float(exit_threshold)
        self.enter_count = max(1, int(enter_count))
        self.exit_count = max(1, int(exit_count))
        self.hold_seconds = max(0.0, float(hold_seconds))
        self.crying = False
        self._enter_hits = 0
        self._exit_hits = 0
        self._hold_until = 0.0

    def update(self, score: float, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        score = float(score)
        if self.crying:
            if score >= self.enter_threshold:
                self._hold_until = now + self.hold_seconds
            if now >= self._hold_until and score < self.exit_threshold:
                self._exit_hits += 1
            else:
                self._exit_hits = 0
            if self._exit_hits >= self.exit_count:
                self.crying = False
                self._exit_hits = 0
        else:
            if score >= self.enter_threshold:
                self._enter_hits += 1
            else:
                self._enter_hits = 0
            if self._enter_hits >= self.enter_count:
                self.crying = True
                self._enter_hits = 0
                self._hold_until = now + self.hold_seconds

        return {
            "crying": self.crying,
            "cry_enter_hits": self._enter_hits,
            "cry_exit_hits": self._exit_hits,
            "cry_hold_remaining_s": round(max(0.0, self._hold_until - now), 2),
            "cry_enter_threshold": self.enter_threshold,
            "cry_exit_threshold": self.exit_threshold,
        }


class ContinuousWaveformRecorder:
    def __init__(
        self,
        device: str,
        sample_rate: int = 16000,
        buffer_seconds: float = 8.0,
        chunk_seconds: float = 0.25,
    ) -> None:
        self.device = device
        self.sample_rate = int(sample_rate)
        self.buffer_seconds = max(1.0, float(buffer_seconds))
        self.chunk_seconds = max(0.01, float(chunk_seconds))
        self._bytes_per_sample = 2
        self._max_bytes = int(self.buffer_seconds * self.sample_rate * self._bytes_per_sample)
        self._chunk_bytes = max(
            self._bytes_per_sample,
            int(self.chunk_seconds * self.sample_rate * self._bytes_per_sample),
        )
        self._chunk_bytes -= self._chunk_bytes % self._bytes_per_sample
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._data_ready = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._started_at = 0.0
        self._last_audio_at = 0.0
        self._total_bytes = 0
        self._error: str | None = None

    def start(self) -> None:
        if self.running:
            return
        self.stop()
        with self._lock:
            self._buffer.clear()
            self._error = None
            self._started_at = time.time()
            self._last_audio_at = 0.0
            self._total_bytes = 0
        self._stop.clear()
        command = [
            "arecord",
            "-q",
            "-D",
            self.device,
            "-f",
            "S16_LE",
            "-r",
            str(self.sample_rate),
            "-c",
            "1",
            "-F",
            "10000",
            "-B",
            "40000",
            "-t",
            "raw",
        ]
        self._process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._thread = threading.Thread(target=self._read_loop, name="audio-capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        process = self._process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._process = None
        self._thread = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def latest_window(self, seconds: float) -> tuple[np.ndarray, int]:
        seconds = max(0.1, float(seconds))
        byte_count = int(seconds * self.sample_rate * self._bytes_per_sample)
        byte_count -= byte_count % self._bytes_per_sample
        with self._lock:
            error = self._error
            data = bytes(self._buffer[-byte_count:])
        if error:
            raise RuntimeError(error)
        if not data:
            return np.zeros(0, dtype=np.float32), self.sample_rate
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        return samples, self.sample_rate

    def live_position(self) -> int:
        with self._lock:
            return self._total_bytes

    def read_pcm_since(self, position: int, max_bytes: int, timeout: float = 1.0) -> tuple[bytes, int]:
        max_bytes = max(self._bytes_per_sample, int(max_bytes))
        max_bytes -= max_bytes % self._bytes_per_sample
        deadline = time.time() + max(0.0, float(timeout))
        with self._data_ready:
            while self._total_bytes <= position and not self._error and self.running:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._data_ready.wait(timeout=remaining)

            if self._error:
                raise RuntimeError(self._error)

            buffer_start = self._total_bytes - len(self._buffer)
            position = max(int(position), buffer_start)
            available = self._total_bytes - position
            if available <= 0:
                return b"", self._total_bytes

            byte_count = min(max_bytes, available)
            byte_count -= byte_count % self._bytes_per_sample
            offset = position - buffer_start
            data = bytes(self._buffer[offset : offset + byte_count])
            return data, position + len(data)

    def status(self) -> dict[str, Any]:
        with self._lock:
            buffered_seconds = len(self._buffer) / (self.sample_rate * self._bytes_per_sample)
            error = self._error
            last_audio_at = self._last_audio_at
        return {
            "running": self.running,
            "device": self.device,
            "sample_rate": self.sample_rate,
            "buffer_seconds": round(buffered_seconds, 3),
            "capacity_seconds": self.buffer_seconds,
            "last_audio_age_s": None if not last_audio_at else round(time.time() - last_audio_at, 3),
            "error": error,
        }

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        while not self._stop.is_set():
            try:
                chunk = process.stdout.read(self._chunk_bytes)
            except Exception as exc:
                with self._lock:
                    self._error = str(exc)
                return
            if not chunk:
                if process.poll() is not None:
                    stderr = ""
                    if process.stderr is not None:
                        stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
                    with self._lock:
                        self._error = stderr or f"arecord exited with code {process.returncode}"
                    return
                continue
            with self._lock:
                self._buffer.extend(chunk)
                if len(self._buffer) > self._max_bytes:
                    del self._buffer[: len(self._buffer) - self._max_bytes]
                self._total_bytes += len(chunk)
                self._last_audio_at = time.time()
                self._data_ready.notify_all()


def record_waveform(seconds: float, device: str, output: Path, sample_rate: int = 16000) -> tuple[np.ndarray, int]:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "arecord",
        "-D",
        device,
        "-f",
        "S16_LE",
        "-r",
        str(sample_rate),
        "-c",
        "1",
        "-d",
        str(max(1, int(round(seconds)))),
        str(output),
    ]
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=seconds + 5)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "arecord failed").strip())
    return read_waveform(output)


def write_waveform(path: Path, waveform: np.ndarray, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(waveform, -1.0, 1.0)
    samples = (clipped * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())


def read_waveform(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    samples = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return samples.astype(np.float32) / 32768.0, rate


def load_labels(path: str | Path) -> list[str]:
    labels: list[str] = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            labels.append(row.get("display_name") or row.get("name") or str(len(labels)))
    return labels
