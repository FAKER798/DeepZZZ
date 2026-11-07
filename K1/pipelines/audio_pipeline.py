from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import queue
import re
from pathlib import Path
import subprocess
import threading
import time
from typing import Any
import wave

import numpy as np

from ai.audio_ai import (
    ContinuousWaveformRecorder,
    CryStateTracker,
    YamnetCryDetector,
    read_waveform,
    record_waveform,
    write_waveform,
)
from devices.mic import record_audio_level
from utils.metrics import recent_rate
from utils.paths import AUDIO_DIR, LULLABY_DIR, LULLABY_MANIFEST_FILE


PLAYBACK_SAMPLE_RATE = 48000
PLAYBACK_CHANNELS = 2
DBFS_TO_ESTIMATED_DB_OFFSET = 94.0
TALK_INPUT_SAMPLE_RATE = 16000
TALK_INPUT_CHANNELS = 1
TALK_INPUT_FORMAT = "S16_LE"
TALK_INPUT_QUEUE_SECONDS = 0.2


class LullabyPlayer:
    def __init__(self, output_device: str) -> None:
        self.output_device = output_device
        self._lock = threading.RLock()
        self._tracks: dict[str, dict[str, Any]] = self._load_manifest()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._stop_event: threading.Event | None = None
        self._running = False
        self._sequence = 0
        self._result: dict[str, Any] | None = None
        self._error: str | None = None
        self._position_frames = 0

    def status(self, talk_status: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            result = dict(self._result or {})
            current_track_id = result.get("track_id")
            tracks = self._tracks_sorted_locked()
            current = dict(self._tracks[current_track_id]) if current_track_id in self._tracks else None
            playback = {
                "running": self._running,
                "capture_running": False,
                "capture_started_at": None,
                "capture": None,
                "last_result": result or None,
                "error": self._error,
                "talk_input": talk_status,
                "updated_at": result.get("updated_at"),
            }
            return {
                "tracks": tracks,
                "current_track": current,
                "current_track_id": current_track_id,
                "playing": bool(self._running and current_track_id),
                "paused": bool(not self._running and result.get("status") == "paused" and current_track_id),
                "position_ms": self._position_ms_locked(current),
                "playback": playback,
            }

    def playback_status(self, talk_status: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.status(talk_status=talk_status)["playback"]

    def list_tracks(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._tracks_sorted_locked()

    def save_and_play(self, payload: bytes, title: str | None = None, volume: float | None = None) -> dict[str, Any]:
        track = self.save(payload, title=title)
        playback = self.play(str(track["id"]), volume=volume)
        return {"track": track, "playback": playback}

    def save(self, payload: bytes, title: str | None = None) -> dict[str, Any]:
        if not payload:
            raise RuntimeError("uploaded lullaby is empty")
        source_digest = hashlib.sha256(payload).hexdigest()
        track_id = source_digest[:16]
        now = time.time()
        safe_title = (title or "未命名摇篮曲").strip() or "未命名摇篮曲"
        LULLABY_DIR.mkdir(parents=True, exist_ok=True)
        uploaded = LULLABY_DIR / f"{track_id}_source.wav"
        output = LULLABY_DIR / f"{track_id}.wav"
        uploaded.write_bytes(payload)
        waveform, rate = read_waveform(uploaded)
        write_playback_waveform(output, waveform, rate, 1.0)
        track = {
            "id": track_id,
            "title": safe_title[:120],
            "file": str(output),
            "audio_url": f"/lullabies/{output.name}",
            "source_file": str(uploaded),
            "bytes": len(payload),
            "stored_bytes": output.stat().st_size if output.exists() else 0,
            "sample_rate": rate,
            "playback_sample_rate": PLAYBACK_SAMPLE_RATE,
            "playback_channels": PLAYBACK_CHANNELS,
            "duration_s": round(float(waveform.size) / max(1, int(rate)), 3),
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            previous = self._tracks.get(track_id)
            if previous and previous.get("created_at"):
                track["created_at"] = previous["created_at"]
            self._tracks[track_id] = track
            self._save_manifest_locked()
            return dict(track)

    def play(
        self,
        track_id: str | None = None,
        volume: float | None = None,
        position_ms: float | None = None,
        loop: bool = True,
    ) -> dict[str, Any]:
        track = self._track(track_id)
        output = Path(str(track["file"]))
        if not output.exists():
            raise RuntimeError("lullaby file is missing on board")
        volume = clamp_volume(volume if volume is not None else 1.0)
        with self._lock:
            current = self._result or {}
            if (
                self._running
                and current.get("track_id") == track["id"]
                and self._process is not None
                and self._process.poll() is None
                and position_ms is None
                and bool(current.get("loop", True)) == bool(loop)
            ):
                mixer = set_output_volume(volume, self.output_device)
                updated = dict(current)
                updated.update({"volume": volume, "mixer": mixer, "updated_at": time.time()})
                self._result = updated
                self._error = None if mixer.get("ok") else str(mixer.get("error") or "mixer update failed")
                return updated.copy()
        self.stop()
        mixer = set_output_volume(volume, self.output_device)
        started_at = time.time()
        start_frame = self._frame_for_position(track, position_ms)
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
            stop_event = threading.Event()
            result = {
                **track,
                "track_id": track["id"],
                "title": track["title"],
                "volume": volume,
                "mixer": mixer,
                "loop": bool(loop),
                "_sequence": sequence,
                "status": "playing",
                "position_ms": self._position_ms_for_frame(track, start_frame),
                "output_device": self.output_device,
                "started_at": started_at,
                "updated_at": started_at,
            }
            self._stop_event = stop_event
            self._position_frames = start_frame
            self._running = True
            self._error = None
            self._result = result
            self._thread = threading.Thread(
                target=self._worker,
                args=(output, result, stop_event, sequence, start_frame, bool(loop)),
                name="lullaby-playback",
                daemon=True,
            )
            self._thread.start()
            return result.copy()

    def pause(self) -> dict[str, Any]:
        with self._lock:
            if not self._running:
                return self.status()
        return self._halt("paused", reset_position=False)

    def seek(self, position_ms: float | None) -> dict[str, Any]:
        if position_ms is None:
            raise RuntimeError("position_ms is required")
        with self._lock:
            result = dict(self._result or {})
            track_id = str(result.get("track_id") or "")
            volume = float(result.get("volume", 1.0))
            was_running = self._running
        track = self._track(track_id)
        target_frame = self._frame_for_position(track, position_ms)
        if was_running:
            return self.play(track_id, volume=volume, position_ms=position_ms)
        with self._lock:
            self._position_frames = target_frame
            result.update(
                {
                    "status": "paused",
                    "position_ms": self._position_ms_for_frame(track, target_frame),
                    "updated_at": time.time(),
                }
            )
            self._result = result
            return self.status()

    def set_volume(self, volume: float | None = None) -> dict[str, Any]:
        volume = clamp_volume(volume if volume is not None else 1.0)
        mixer = set_output_volume(volume, self.output_device)
        with self._lock:
            result = dict(self._result or {})
            result.update({"volume": volume, "mixer": mixer, "updated_at": time.time()})
            self._result = result
            self._error = None if mixer.get("ok") else str(mixer.get("error") or "mixer update failed")
            return self.playback_status()

    def stop(self) -> dict[str, Any]:
        return self._halt("stopped", reset_position=True)

    def _halt(self, status: str, reset_position: bool) -> dict[str, Any]:
        with self._lock:
            stop_event = self._stop_event
            process = self._process
            thread = self._thread
            if stop_event is not None:
                stop_event.set()
            if process is not None and process.poll() is None:
                process.terminate()
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.5)
        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=1.0)
        with self._lock:
            result = dict(self._result or {})
            if result:
                if reset_position:
                    self._position_frames = 0
                track = self._tracks.get(str(result.get("track_id") or ""))
                result.update(
                    {
                        "status": status,
                        "position_ms": self._position_ms_locked(track),
                        "updated_at": time.time(),
                    }
                )
                self._result = result
            self._running = False
            self._process = None
            self._stop_event = None
            return self.playback_status()

    def pause_for_priority(self) -> dict[str, Any] | None:
        with self._lock:
            result = dict(self._result or {})
            if not self._running or not result.get("track_id"):
                return None
            resume = {
                "track_id": str(result["track_id"]),
                "volume": float(result.get("volume", 1.0)),
                "position_ms": self._position_ms_locked(self._tracks.get(str(result["track_id"]))),
                "loop": bool(result.get("loop", True)),
            }
        self.pause()
        return resume

    def resume(self, resume: dict[str, Any] | None) -> None:
        if not resume:
            return
        try:
            self.play(
                str(resume.get("track_id") or ""),
                volume=resume.get("volume"),
                position_ms=resume.get("position_ms"),
                loop=bool(resume.get("loop", True)),
            )
        except Exception as exc:
            with self._lock:
                self._error = f"resume lullaby failed: {exc}"

    def _worker(
        self,
        output: Path,
        result: dict[str, Any],
        stop_event: threading.Event,
        sequence: int,
        start_frame: int,
        loop: bool,
    ) -> None:
        with wave.open(str(output), "rb") as source:
            sample_rate = source.getframerate()
            channels = source.getnchannels()
        command = [
            "aplay", "-q", "-D", self.output_device,
            "-f", "S16_LE", "-r", str(sample_rate), "-c", str(channels), "-t", "raw",
        ]
        started = time.perf_counter()
        loops = 0
        returncode = 0
        error = ""
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            with self._lock:
                self._process = process
            with wave.open(str(output), "rb") as source:
                source.setpos(min(start_frame, source.getnframes()))
                while not stop_event.is_set():
                    chunk = source.readframes(2048)
                    if not chunk:
                        if not loop:
                            break
                        loops += 1
                        source.rewind()
                        with self._lock:
                            self._position_frames = 0
                        continue
                    if process.stdin is None:
                        raise RuntimeError("aplay stdin is unavailable")
                    process.stdin.write(chunk)
                    process.stdin.flush()
                    with self._lock:
                        self._position_frames = source.tell()
            if process.stdin is not None:
                process.stdin.close()
            returncode = process.wait(timeout=2.0)
            error = (process.stderr.read() if process.stderr else b"").decode("utf-8", errors="replace").strip()
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            with self._lock:
                if self._sequence != sequence:
                    return
                stopped = stop_event.is_set()
                current_result = self._result if isinstance(self._result, dict) else {}
                if "volume" in current_result:
                    result["volume"] = current_result["volume"]
                if "mixer" in current_result:
                    result["mixer"] = current_result["mixer"]
                result.update(
                    {
                        "status": result.get("status", "stopped") if stopped else "error" if returncode != 0 else "ok",
                        "returncode": returncode,
                        "elapsed_ms": elapsed_ms,
                        "loops": loops,
                        "updated_at": time.time(),
                    }
                )
                if returncode != 0 and not stopped:
                    self._error = error or "aplay failed"
                    result["error"] = self._error
                self._result = result.copy()
                self._running = False
                self._process = None
        except Exception as exc:
            with self._lock:
                if self._sequence != sequence:
                    return
                if stop_event.is_set():
                    result.update({"status": "stopped", "updated_at": time.time()})
                    result.pop("error", None)
                    self._error = None
                else:
                    result.update({"status": "error", "error": str(exc), "updated_at": time.time()})
                    self._error = str(exc)
                self._result = result.copy()
                self._running = False
                self._process = None

    def _frame_for_position(self, track: dict[str, Any], position_ms: float | None) -> int:
        duration_ms = max(1.0, float(track.get("duration_s") or 0.0) * 1000.0)
        value = max(0.0, min(float(position_ms or 0.0), duration_ms))
        return int(value * int(track.get("playback_sample_rate") or PLAYBACK_SAMPLE_RATE) / 1000.0)

    def _position_ms_for_frame(self, track: dict[str, Any] | None, frame: int) -> int:
        if not track:
            return 0
        rate = max(1, int(track.get("playback_sample_rate") or PLAYBACK_SAMPLE_RATE))
        return int(max(0, frame) * 1000 / rate)

    def _position_ms_locked(self, track: dict[str, Any] | None) -> int:
        return self._position_ms_for_frame(track, self._position_frames)

    def _track(self, track_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            if track_id:
                track = self._tracks.get(str(track_id))
            else:
                track = next(iter(self._tracks_sorted_locked()), None)
            if not track:
                raise RuntimeError("no board lullaby is stored")
            return dict(track)

    def _load_manifest(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(LULLABY_MANIFEST_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
        tracks = data.get("tracks") if isinstance(data, dict) else None
        if not isinstance(tracks, list):
            return {}
        loaded: dict[str, dict[str, Any]] = {}
        for track in tracks:
            if not isinstance(track, dict):
                continue
            track_id = str(track.get("id") or "")
            file_path = Path(str(track.get("file") or ""))
            if not track_id or not file_path.exists():
                continue
            loaded[track_id] = dict(track)
        return loaded

    def _save_manifest_locked(self) -> None:
        LULLABY_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 2,
            "updated_at": time.time(),
            "tracks": self._tracks_sorted_locked(),
        }
        tmp = LULLABY_MANIFEST_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(LULLABY_MANIFEST_FILE)

    def _tracks_sorted_locked(self) -> list[dict[str, Any]]:
        return sorted(
            [dict(track) for track in self._tracks.values()],
            key=lambda track: float(track.get("updated_at") or 0.0),
            reverse=True,
        )


class AudioPipeline:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.audio_lock = threading.Lock()
        self.yamnet_lock = threading.Lock()
        self.latest_audio: dict[str, Any] | None = None
        self.latest_yamnet: dict[str, Any] | None = None
        self._yamnet: YamnetCryDetector | None = None
        self._audio_recorder: ContinuousWaveformRecorder | None = None
        self._cry_state = CryStateTracker()
        self._yamnet_checks = 0
        self._yamnet_checked_at: deque[float] = deque(maxlen=120)
        self._yamnet_stop = threading.Event()
        self._yamnet_thread: threading.Thread | None = None
        self._playback_lock = threading.Lock()
        self._playback_thread: threading.Thread | None = None
        self._playback_running = False
        self._playback_result: dict[str, Any] | None = None
        self._playback_error: str | None = None
        self._playback_process: subprocess.Popen[str] | None = None
        self._playback_stop = threading.Event()
        self._playback_sequence = 0
        self._playback_capture_started_at: float | None = None
        self._playback_capture: dict[str, Any] | None = None
        self.output_device = str(args.audio_output_device)
        self._lullaby_player = LullabyPlayer(self.output_device)
        self._talk_lullaby_resume: dict[str, Any] | None = None
        self._talk_lock = threading.Lock()
        self._talk_queue: queue.Queue[bytes] = queue.Queue(
            maxsize=max(1, int(TALK_INPUT_QUEUE_SECONDS / 0.04)),
        )
        self._talk_process: subprocess.Popen[bytes] | None = None
        self._talk_started_at: float | None = None
        self._talk_last_chunk_at: float | None = None
        self._talk_chunks = 0
        self._talk_dropped_chunks = 0
        self._talk_error: str | None = None
        self._talk_stop = threading.Event()
        self._talk_thread: threading.Thread | None = None

    def start(self) -> None:
        if self.args.enable_yamnet:
            self.start_yamnet_loop()

    def stop(self) -> None:
        self._lullaby_player.stop()
        self.stop_talk_input()
        self.stop_yamnet_loop()

    def start_yamnet_loop(self) -> None:
        if self._yamnet_thread and self._yamnet_thread.is_alive():
            return
        self._yamnet_stop.clear()
        self._yamnet_thread = threading.Thread(target=self._yamnet_loop, name="yamnet-loop", daemon=True)
        self._yamnet_thread.start()

    def stop_yamnet_loop(self) -> None:
        self._yamnet_stop.set()
        if self._yamnet_thread and self._yamnet_thread.is_alive():
            self._yamnet_thread.join(timeout=2)
        if self._audio_recorder is not None:
            self._audio_recorder.stop()

    def audio_rate_hz(self, now: float | None = None) -> float | None:
        return recent_rate(self._yamnet_checked_at, now=now)

    def playback_status(self) -> dict[str, Any]:
        with self._playback_lock:
            talk_status = self.talk_input_status()
            return {
                "running": self._playback_running,
                "capture_running": self._playback_capture_started_at is not None,
                "capture_started_at": self._playback_capture_started_at,
                "capture": self._playback_capture,
                "last_result": self._playback_result,
                "error": self._playback_error,
                "talk_input": talk_status,
                "updated_at": (self._playback_result or {}).get("updated_at"),
            }

    def lullaby_status(self) -> dict[str, Any]:
        return self._lullaby_player.status(talk_status=self.talk_input_status())

    def stop_playback(self) -> dict[str, Any]:
        self._lullaby_player.stop()
        with self._talk_lock:
            self._talk_lullaby_resume = None
        with self._playback_lock:
            self._playback_stop.set()
            process = self._playback_process
            if process is None or process.poll() is not None:
                self._playback_running = False
                return_status = True
            else:
                return_status = False
                process.terminate()
        if return_status:
            return self.playback_status()
        try:
            process.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.5)
        with self._playback_lock:
            result = dict(self._playback_result or {})
            result.update({"status": "stopped", "updated_at": time.time()})
            self._playback_result = result
            self._playback_running = False
            self._playback_process = None
        return self.playback_status()

    def set_playback_volume(self, volume: float | None = None) -> dict[str, Any]:
        lullaby = self.lullaby_status()
        if lullaby.get("playing"):
            return self._lullaby_player.set_volume(volume)
        volume = clamp_volume(self.args.audio_playback_volume if volume is None else volume)
        mixer = set_output_volume(volume, self.output_device)
        with self._playback_lock:
            result = dict(self._playback_result or {})
            result.update({"volume": volume, "mixer": mixer, "updated_at": time.time()})
            self._playback_result = result
            self._playback_error = None if mixer.get("ok") else str(mixer.get("error") or "mixer update failed")
        return self.playback_status()

    def start_playback_capture(self) -> dict[str, Any]:
        with self._playback_lock:
            if self._playback_capture_started_at is None:
                self._playback_capture_started_at = time.time()
                self._playback_error = None
            return self._capture_status_locked()

    def stop_playback_capture(self, volume: float | None = None) -> dict[str, Any]:
        with self._playback_lock:
            started_at = self._playback_capture_started_at
            if started_at is None:
                raise RuntimeError("audio capture is not running")
            self._playback_capture_started_at = None
        duration = max(0.5, min(time.time() - started_at, 10.0))
        capture = self._write_playback_sample(duration, volume=volume)
        capture.update(
            {
                "status": "ready",
                "captured_at": time.time(),
                "capture_started_at": started_at,
                "seconds": round(duration, 3),
            }
        )
        with self._playback_lock:
            self._playback_capture = capture
            self._playback_error = None
            return capture.copy()

    def play_captured_audio(
        self,
        output_device: str | None = None,
        volume: float | None = None,
    ) -> dict[str, Any]:
        with self._playback_lock:
            capture = dict(self._playback_capture or {})
        if not capture.get("file"):
            raise RuntimeError("no captured audio sample")
        source = Path(str(capture["file"]))
        if not source.exists():
            raise RuntimeError("captured audio sample is missing")
        output_device = self.output_device
        if volume is not None and clamp_volume(volume) != float(capture.get("volume", 1.0)):
            duration = float(capture.get("seconds") or 0.5)
            capture = self._write_playback_sample(duration, volume=volume)
            capture.update({"status": "ready", "captured_at": time.time(), "seconds": round(duration, 3)})
            source = Path(str(capture["file"]))
            with self._playback_lock:
                self._playback_capture = capture
        return self._start_playback(source, output_device, capture)

    def play_recent_audio(
        self,
        seconds: float,
        output_device: str | None = None,
        volume: float | None = None,
    ) -> dict[str, Any]:
        seconds = max(0.5, min(float(seconds), 10.0))
        output_device = self.output_device
        capture = self._write_playback_sample(seconds, volume=volume)
        capture.update({"status": "ready", "captured_at": time.time(), "seconds": round(seconds, 3)})
        return self._start_playback(Path(str(capture["file"])), output_device, capture)

    def play_uploaded_audio(
        self,
        payload: bytes,
        output_device: str | None = None,
        volume: float | None = None,
        loop: bool = False,
    ) -> dict[str, Any]:
        if not payload:
            raise RuntimeError("uploaded audio is empty")
        if loop:
            saved = self._lullaby_player.save_and_play(payload, title="上传摇篮曲", volume=volume)
            return dict(saved["playback"])
        else:
            resume_after = self._lullaby_player.pause_for_priority()
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        uploaded = AUDIO_DIR / f"talk_upload_{int(time.time() * 1000)}.wav"
        output = AUDIO_DIR / f"talk_{int(time.time() * 1000)}.wav"
        uploaded.write_bytes(payload)
        waveform, rate = read_waveform(uploaded)
        volume = clamp_volume(self.args.audio_playback_volume if volume is None else volume)
        mixer = set_output_volume(volume, self.output_device)
        render_volume = 1.0 if mixer.get("ok") else volume
        write_playback_waveform(output, waveform, rate, render_volume)
        capture = {
            "file": str(output),
            "audio_url": f"/audio/{output.name}",
            "source_file": str(uploaded),
            "bytes": len(payload),
            "sample_rate": rate,
            "playback_sample_rate": PLAYBACK_SAMPLE_RATE,
            "playback_channels": PLAYBACK_CHANNELS,
            "volume": volume,
            "mixer": mixer,
            "loop": bool(loop),
        }
        return self._start_playback(output, self.output_device, capture, loop=False, resume_after=resume_after)

    def list_lullabies(self) -> list[dict[str, Any]]:
        return self._lullaby_player.list_tracks()

    def save_lullaby(
        self,
        payload: bytes,
        title: str | None = None,
        volume: float | None = None,
    ) -> dict[str, Any]:
        return self._lullaby_player.save_and_play(payload, title=title, volume=volume)

    def play_lullaby(
        self,
        track_id: str | None = None,
        volume: float | None = None,
        position_ms: float | None = None,
        loop: bool = True,
    ) -> dict[str, Any]:
        return self._lullaby_player.play(
            track_id=track_id,
            volume=volume,
            position_ms=position_ms,
            loop=loop,
        )

    def pause_lullaby(self) -> dict[str, Any]:
        return self._lullaby_player.pause()

    def seek_lullaby(self, position_ms: float | None) -> dict[str, Any]:
        return self._lullaby_player.seek(position_ms)

    def start_talk_input(self) -> dict[str, Any]:
        with self._talk_lock:
            if self._talk_process is not None and self._talk_process.poll() is None:
                return self._talk_input_status_locked()
            needs_cleanup = self._talk_process is not None or self._talk_thread is not None
        if needs_cleanup:
            self.stop_talk_input()
        resume_lullaby = self._lullaby_player.pause_for_priority()
        with self._talk_lock:
            if self._talk_process is not None and self._talk_process.poll() is None:
                if resume_lullaby is not None:
                    self._lullaby_player.resume(resume_lullaby)
                return self._talk_input_status_locked()
            self._clear_talk_queue()
            self._talk_stop.clear()
            self._talk_error = None
            self._talk_chunks = 0
            self._talk_dropped_chunks = 0
            self._talk_started_at = time.time()
            self._talk_last_chunk_at = None
            self._talk_lullaby_resume = resume_lullaby
            command = [
                "aplay",
                "-q",
                "-D",
                self.output_device,
                "-f",
                TALK_INPUT_FORMAT,
                "-r",
                str(TALK_INPUT_SAMPLE_RATE),
                "-c",
                str(TALK_INPUT_CHANNELS),
                "-t",
                "raw",
            ]
            self._talk_process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._talk_thread = threading.Thread(target=self._talk_input_worker, name="talk-input-playback", daemon=True)
            self._talk_thread.start()
            return self._talk_input_status_locked()

    def stop_talk_input(self) -> dict[str, Any]:
        with self._talk_lock:
            self._talk_stop.set()
            process = self._talk_process
            thread = self._talk_thread
        if process and process.stdin:
            try:
                process.stdin.close()
            except Exception:
                pass
        if thread and thread.is_alive():
            thread.join(timeout=2)
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        with self._talk_lock:
            self._talk_process = None
            self._talk_thread = None
            self._talk_started_at = None
            resume_lullaby = self._talk_lullaby_resume
            self._talk_lullaby_resume = None
            self._clear_talk_queue()
            status = self._talk_input_status_locked()
        self._lullaby_player.resume(resume_lullaby)
        return status

    def receive_talk_pcm(self, payload: bytes) -> dict[str, Any]:
        if not payload:
            raise RuntimeError("talk audio chunk is empty")
        if len(payload) % 2:
            payload = payload[:-1]
        self.start_talk_input()
        try:
            self._talk_queue.put_nowait(payload)
        except queue.Full:
            try:
                self._talk_queue.get_nowait()
            except queue.Empty:
                pass
            self._talk_queue.put_nowait(payload)
            with self._talk_lock:
                self._talk_dropped_chunks += 1
        with self._talk_lock:
            self._talk_chunks += 1
            self._talk_last_chunk_at = time.time()
            return self._talk_input_status_locked()

    def talk_input_status(self) -> dict[str, Any]:
        with self._talk_lock:
            return self._talk_input_status_locked()

    def ensure_live_audio_capture(self) -> dict[str, Any]:
        self._ensure_audio_recorder()
        if self._audio_recorder is None:
            raise RuntimeError("audio recorder is not initialized")
        return self._audio_recorder.status()

    def live_audio_position(self) -> int:
        self.ensure_live_audio_capture()
        if self._audio_recorder is None:
            return 0
        return self._audio_recorder.live_position()

    def read_live_pcm_since(self, position: int, max_bytes: int, timeout: float = 1.0) -> tuple[bytes, int]:
        self.ensure_live_audio_capture()
        if self._audio_recorder is None:
            return b"", int(position)
        return self._audio_recorder.read_pcm_since(position, max_bytes=max_bytes, timeout=timeout)

    def _write_playback_sample(self, seconds: float, volume: float | None = None) -> dict[str, Any]:
        seconds = max(0.5, min(float(seconds), 10.0))
        volume = clamp_volume(self.args.audio_playback_volume if volume is None else volume)
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        output = AUDIO_DIR / f"playback_{int(time.time() * 1000)}.wav"
        if self._audio_recorder and self._audio_recorder.running:
            waveform, rate = self._audio_recorder.latest_window(seconds)
            if waveform.size == 0:
                raise RuntimeError("no buffered audio available for playback")
        else:
            with self.audio_lock:
                waveform, rate = record_waveform(
                    seconds=seconds,
                    device=self.args.audio_device,
                    output=output,
                    sample_rate=self.args.audio_rate,
                )
        write_playback_waveform(output, waveform, rate, volume)
        return {
            "file": str(output),
            "audio_url": f"/audio/{output.name}",
            "seconds": round(seconds, 3),
            "sample_rate": rate,
            "playback_sample_rate": PLAYBACK_SAMPLE_RATE,
            "playback_channels": PLAYBACK_CHANNELS,
            "volume": volume,
        }

    def _start_playback(
        self,
        output: Path,
        output_device: str,
        sample: dict[str, Any],
        loop: bool = False,
        resume_after: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        deadline = time.time() + 3.0
        with self._playback_lock:
            while self._playback_running:
                if self._playback_thread and not self._playback_thread.is_alive():
                    self._playback_running = False
                    break
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise RuntimeError("audio playback already running")
                self._playback_lock.release()
                try:
                    time.sleep(min(0.1, remaining))
                finally:
                    self._playback_lock.acquire()
            self._playback_stop.clear()
            started_at = time.time()
            self._playback_sequence += 1
            sequence = self._playback_sequence
            result = {
                **sample,
                "_sequence": sequence,
                "status": "playing",
                "output_device": output_device,
                "loop": bool(loop),
                "started_at": started_at,
                "updated_at": started_at,
            }
            self._playback_running = True
            self._playback_error = None
            self._playback_result = result
            self._playback_thread = threading.Thread(
                target=self._playback_worker,
                args=(output, output_device, result, bool(loop), resume_after, sequence),
                name="audio-playback",
                daemon=True,
            )
            self._playback_thread.start()
            return result.copy()

    def _capture_status_locked(self) -> dict[str, Any]:
        started_at = self._playback_capture_started_at
        return {
            "status": "capturing" if started_at is not None else "idle",
            "capture_running": started_at is not None,
            "capture_started_at": started_at,
            "capture_elapsed_s": round(time.time() - started_at, 3) if started_at is not None else None,
        }

    def record_audio(self, seconds: float) -> dict[str, Any]:
        seconds = max(0.5, min(float(seconds), 10.0))
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        output = AUDIO_DIR / f"mic_{int(time.time() * 1000)}.wav"
        if self._audio_recorder and self._audio_recorder.running:
            waveform, rate = self._audio_recorder.latest_window(seconds)
            write_waveform(output, waveform, rate)
            result = self._audio_level_from_waveform(output, waveform, rate)
            self.latest_audio = result
            return result
        with self.audio_lock:
            result = record_audio_level(
                output,
                seconds=seconds,
                device=self.args.audio_device,
                sample_rate=self.args.audio_rate,
            )
            self.latest_audio = result
            return result

    def analyze_cry(self, seconds: float) -> dict[str, Any]:
        seconds = max(1.0, min(float(seconds), 10.0))
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        if self._audio_recorder and self._audio_recorder.running:
            waveform, rate = self._audio_recorder.latest_window(seconds)
            return self._analyze_cry_waveform(waveform, rate, save_audio=True)
        with self.audio_lock:
            output = AUDIO_DIR / "yamnet_latest.wav"
            waveform, rate = record_waveform(
                seconds=seconds,
                device=self.args.audio_device,
                output=output,
                sample_rate=self.args.audio_rate,
            )
            return self._analyze_cry_waveform(waveform, rate, output=output, save_audio=False)

    def _yamnet_loop(self) -> None:
        while not self._yamnet_stop.is_set():
            try:
                self._ensure_audio_recorder()
                waveform, rate = self._latest_yamnet_window()
                min_samples = int(self.args.audio_rate * min(1.0, float(self.args.yamnet_seconds)))
                if waveform.size < min_samples:
                    previous = self.latest_yamnet or {}
                    self.latest_yamnet = {
                        **previous,
                        "status": "warming",
                        "capture": self._audio_recorder.status() if self._audio_recorder else None,
                        "updated_at": time.time(),
                    }
                else:
                    self._analyze_cry_waveform(waveform, rate, save_audio=False)
            except Exception as exc:
                self.latest_yamnet = {
                    "status": "error",
                    "error": str(exc),
                    "capture": self._audio_recorder.status() if self._audio_recorder else None,
                    "updated_at": time.time(),
                }
                if self._audio_recorder is not None:
                    self._audio_recorder.stop()
                    self._audio_recorder = None
                if self._yamnet_stop.wait(1.0):
                    break
                continue
            self._yamnet_stop.wait(max(0.1, float(self.args.yamnet_interval)))

    def _ensure_audio_recorder(self) -> None:
        if self._audio_recorder is None:
            buffer_seconds = max(8.0, float(self.args.yamnet_seconds) * 3.0)
            self._audio_recorder = ContinuousWaveformRecorder(
                device=self.args.audio_device,
                sample_rate=self.args.audio_rate,
                buffer_seconds=buffer_seconds,
                chunk_seconds=0.01,
            )
        if not self._audio_recorder.running:
            self._audio_recorder.start()

    def _latest_yamnet_window(self) -> tuple[np.ndarray, int]:
        if self._audio_recorder is None:
            raise RuntimeError("audio recorder is not initialized")
        return self._audio_recorder.latest_window(self.args.yamnet_seconds)

    def _analyze_cry_waveform(
        self,
        waveform: np.ndarray,
        rate: int,
        output: Path | None = None,
        save_audio: bool = True,
    ) -> dict[str, Any]:
        if output is None:
            output = AUDIO_DIR / "yamnet_latest.wav"
        if save_audio:
            write_waveform(output, waveform, rate)
        with self.yamnet_lock:
            if self._yamnet is None:
                self._yamnet = YamnetCryDetector(self.args.yamnet_model, self.args.yamnet_labels)
            result = self._yamnet.infer_waveform(waveform, rate)
            result.update(self._cry_state.update(float(result["cry_score"])))
            self._yamnet_checks += 1
            self._yamnet_checked_at.append(time.time())
            result["checks"] = self._yamnet_checks
        result.update(waveform_level(waveform))
        result["file"] = str(output)
        result["audio_url"] = f"/audio/{output.name}"
        result["status"] = "ok"
        result["capture"] = self._audio_recorder.status() if self._audio_recorder else None
        self.latest_yamnet = result
        return result

    def _playback_worker(
        self,
        output: Path,
        output_device: str,
        result: dict[str, Any],
        loop: bool,
        resume_after: dict[str, Any] | None,
        sequence: int,
    ) -> None:
        command = ["aplay", "-q", "-D", output_device, str(output)]
        started = time.perf_counter()
        loops = 0
        should_resume = False
        try:
            completed = subprocess.CompletedProcess(command, 0, "", "")
            while not self._playback_stop.is_set():
                completed = self._run_playback_command(command)
                loops += 1
                if completed.returncode != 0 or not loop:
                    break
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            with self._playback_lock:
                if self._playback_sequence != sequence:
                    return
                stopped = self._playback_stop.is_set()
                current_result = self._playback_result if isinstance(self._playback_result, dict) else {}
                if "volume" in current_result:
                    result["volume"] = current_result["volume"]
                if "mixer" in current_result:
                    result["mixer"] = current_result["mixer"]
                result.update(
                    {
                        "status": "stopped" if stopped else "ok" if completed.returncode == 0 else "error",
                        "returncode": completed.returncode,
                        "elapsed_ms": elapsed_ms,
                        "loops": loops,
                        "updated_at": time.time(),
                    }
                )
                if completed.returncode != 0 and not stopped:
                    self._playback_error = (completed.stderr or completed.stdout or "aplay failed").strip()
                    result["error"] = self._playback_error
                self._playback_result = result.copy()
                self._playback_running = False
                self._playback_process = None
                should_resume = bool(resume_after) and not loop and not stopped and completed.returncode == 0
        except Exception as exc:
            with self._playback_lock:
                if self._playback_sequence != sequence:
                    return
                result.update({"status": "error", "error": str(exc), "updated_at": time.time()})
                self._playback_error = str(exc)
                self._playback_result = result.copy()
                self._playback_running = False
                self._playback_process = None
        if should_resume and resume_after is not None:
            self._lullaby_player.resume(resume_after)

    def _run_playback_command(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        for _ in range(5):
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            with self._playback_lock:
                self._playback_process = process
            stdout, stderr = process.communicate()
            completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            if completed.returncode == 0 or "设备或资源忙" not in (completed.stderr or ""):
                return completed
            time.sleep(0.05)
        return completed

    def _talk_input_status_locked(self) -> dict[str, Any]:
        running = self._talk_process is not None and self._talk_process.poll() is None
        last_chunk_at = self._talk_last_chunk_at
        return {
            "running": running,
            "sample_rate": TALK_INPUT_SAMPLE_RATE,
            "channels": TALK_INPUT_CHANNELS,
            "queued_chunks": self._talk_queue.qsize(),
            "chunks": self._talk_chunks,
            "dropped_chunks": self._talk_dropped_chunks,
            "started_at": self._talk_started_at,
            "last_chunk_age_s": None if not last_chunk_at else round(time.time() - last_chunk_at, 3),
            "error": self._talk_error,
        }

    def _clear_talk_queue(self) -> None:
        while True:
            try:
                self._talk_queue.get_nowait()
            except queue.Empty:
                break

    def _talk_input_worker(self) -> None:
        process = self._talk_process
        if process is None or process.stdin is None:
            return
        while not self._talk_stop.is_set():
            try:
                chunk = self._talk_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                process.stdin.write(chunk)
                process.stdin.flush()
            except Exception as exc:
                with self._talk_lock:
                    self._talk_error = str(exc)
                break
            if process.poll() is not None:
                stderr = ""
                if process.stderr is not None:
                    stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
                with self._talk_lock:
                    self._talk_error = stderr or f"aplay exited with code {process.returncode}"
                break
        try:
            if process.stdin:
                process.stdin.close()
        except Exception:
            pass

    def _audio_level_from_waveform(self, output: Path, waveform: np.ndarray, rate: int) -> dict[str, Any]:
        return {
            "file": str(output),
            "audio_url": f"/audio/{output.name}",
            "sample_rate": rate,
            "duration_s": round(waveform.size / max(1, rate), 3),
            "elapsed_ms": 0,
            **waveform_level(waveform),
            "engine": "continuous_level_meter",
        }


def clamp_volume(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 1.0
    if not np.isfinite(number):
        number = 1.0
    return round(max(0.0, min(2.0, number)), 2)


def apply_volume(waveform: np.ndarray, volume: float) -> np.ndarray:
    return np.clip(waveform.astype(np.float32) * clamp_volume(volume), -1.0, 1.0)


def set_output_volume(volume: float, output_device: str) -> dict[str, Any]:
    volume = clamp_volume(volume)
    match = re.search(r"CARD=([^,]+)", output_device)
    if match is None:
        return {
            "ok": False,
            "volume": volume,
            "error": f"cannot resolve ALSA card from output device: {output_device}",
        }
    card = match.group(1)
    percent = int(round(min(1.0, volume) * 100.0))
    command = ["amixer", "-c", card, "sset", "PCM", f"{percent}%", "unmute"]
    try:
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2.0)
    except Exception as exc:
        return {"ok": False, "volume": volume, "percent": percent, "card": card, "error": str(exc)}
    if completed.returncode != 0:
        return {
            "ok": False,
            "volume": volume,
            "percent": percent,
            "card": card,
            "error": (completed.stderr or completed.stdout or "amixer failed").strip(),
        }
    return {"ok": True, "volume": volume, "percent": percent, "card": card}


def waveform_level(waveform: np.ndarray) -> dict[str, float]:
    if waveform.size == 0:
        rms = peak = zero_crossing_rate = 0.0
    else:
        rms = float(np.sqrt(np.mean(waveform * waveform)))
        peak = float(np.max(np.abs(waveform)))
        signs = np.signbit(waveform)
        zero_crossing_rate = float(np.count_nonzero(signs[1:] != signs[:-1]) / max(1, len(signs) - 1))
    dbfs = -120.0 if rms <= 1e-8 else 20.0 * float(np.log10(rms))
    noise_db = max(0.0, min(120.0, dbfs + DBFS_TO_ESTIMATED_DB_OFFSET))
    return {
        "rms": round(rms, 5),
        "peak": round(peak, 5),
        "dbfs": round(dbfs, 2),
        "noise_db": round(noise_db, 1),
        "zero_crossing_rate": round(zero_crossing_rate, 5),
    }


def write_playback_waveform(path: Path, waveform: np.ndarray, sample_rate: int, volume: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mono = apply_volume(waveform, volume)
    mono = resample_mono(mono, int(sample_rate), PLAYBACK_SAMPLE_RATE)
    stereo = np.repeat(mono.reshape(-1, 1), PLAYBACK_CHANNELS, axis=1)
    samples = (np.clip(stereo, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(PLAYBACK_CHANNELS)
        handle.setsampwidth(2)
        handle.setframerate(PLAYBACK_SAMPLE_RATE)
        handle.writeframes(samples.tobytes())


def resample_mono(waveform: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if waveform.size == 0 or source_rate == target_rate:
        return waveform.astype(np.float32)
    source_rate = max(1, int(source_rate))
    target_rate = max(1, int(target_rate))
    target_size = max(1, int(round(waveform.size * target_rate / source_rate)))
    source_points = np.arange(waveform.size, dtype=np.float32) / float(source_rate)
    target_points = np.arange(target_size, dtype=np.float32) / float(target_rate)
    return np.interp(target_points, source_points, waveform.astype(np.float32)).astype(np.float32)
