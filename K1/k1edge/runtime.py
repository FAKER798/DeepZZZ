from __future__ import annotations

import time
from typing import Any

from devices.day_night_sensor import day_night_status
from devices.pwm_dual import DualPwmClient
from devices.temperature_humidity import TemperatureHumidityController
from k1edge.config import EdgeConfig
from pipelines.audio_pipeline import AudioPipeline
from pipelines.video_pipeline import VideoPipeline
from services.dataset_capture import DatasetCaptureService
from services.resources import ResourceMonitor
from services.activity_zone import ActivityZoneStore, safe_zone_detection_status
from services.care_log import CareStore
from services.chat_assistant import ChatAssistant, WorkloadPauser, load_assistant_settings
from services.voice_trigger import read_voice_trigger_status, write_voice_trigger_command
from utils.metrics import age_seconds
from utils.paths import ACTIVITY_ZONE_FILE, CARE_PROFILE_FILE, CARE_RECORDS_FILE


FACE_POINT_THRESHOLD = 0.25


def vision_safety_status(latest: dict[str, Any] | None) -> dict[str, Any]:
    updated_at = latest.get("updated_at") if isinstance(latest, dict) else None
    captured_at = latest.get("captured_at") if isinstance(latest, dict) else None
    base = {
        "updated_at": updated_at,
        "captured_at": captured_at,
        "face_point_threshold": FACE_POINT_THRESHOLD,
        "face_points": {},
        "visible_points": [],
        "no_person": False,
        "face_cover_rollover": False,
    }
    if not isinstance(latest, dict):
        return {
            **base,
            "mode": "caution",
            "status": "No person",
            "reason": "No pose result yet",
            "no_person": True,
        }

    persons = latest.get("persons") or []
    person = persons[0] if isinstance(persons, list) and persons else None
    keypoints = person.get("keypoints") if isinstance(person, dict) else None
    if not isinstance(keypoints, list):
        return {
            **base,
            "mode": "caution",
            "status": "No person",
            "reason": "No pose result yet",
            "no_person": True,
        }

    names = ["nose", "leftEye", "rightEye", "leftEar", "rightEar"]
    face_points = {name: face_point(keypoints, index) for index, name in enumerate(names)}
    visible_points = [name for name, point in face_points.items() if point["present"]]
    front_visible = (
        face_points["nose"]["present"]
        or face_points["leftEye"]["present"]
        or face_points["rightEye"]["present"]
    )
    ear_visible = face_points["leftEar"]["present"] or face_points["rightEar"]["present"]

    mode = "clear"
    status = "Face visible"
    reason = "Nose or eye point is visible"
    if not front_visible and ear_visible:
        mode = "covered"
        status = "Suspected face cover / rollover"
        reason = "Nose and eyes are missing; only ear point is visible"
    elif not front_visible:
        mode = "covered"
        status = "Suspected face cover / rollover"
        reason = "No face keypoint is visible"

    return {
        **base,
        "mode": mode,
        "status": status,
        "reason": reason,
        "face_points": face_points,
        "visible_points": visible_points,
        "no_person": False,
        "face_cover_rollover": not front_visible,
    }


def face_point(keypoints: list[Any], index: int) -> dict[str, Any]:
    point = keypoints[index] if index < len(keypoints) else [0, 0, 0]
    confidence = float(point[2] or 0.0) if isinstance(point, list) and len(point) > 2 else 0.0
    return {"confidence": round(confidence, 4), "present": confidence >= FACE_POINT_THRESHOLD}


class VideoRuntime:
    def __init__(self, config: EdgeConfig) -> None:
        self.pipeline = VideoPipeline(config.to_namespace())

    @property
    def camera(self):
        return self.pipeline.camera

    @property
    def vision(self):
        return self.pipeline.vision

    def start(self) -> None:
        self.pipeline.start()

    def stop(self) -> None:
        self.pipeline.stop()

    def status(self) -> dict[str, Any]:
        return self.pipeline.status()


class AudioRuntime:
    def __init__(self, config: EdgeConfig) -> None:
        self.pipeline = AudioPipeline(config.to_namespace())

    @property
    def latest_audio(self) -> dict[str, Any] | None:
        return self.pipeline.latest_audio

    @property
    def latest_yamnet(self) -> dict[str, Any] | None:
        return self.pipeline.latest_yamnet

    def start(self) -> None:
        self.pipeline.start()

    def stop(self) -> None:
        self.pipeline.stop()

    def record_audio(self, seconds: float) -> dict[str, Any]:
        return self.pipeline.record_audio(seconds)

    def analyze_cry(self, seconds: float) -> dict[str, Any]:
        return self.pipeline.analyze_cry(seconds)

    def play_recent_audio(
        self,
        seconds: float,
        output_device: str | None = None,
        volume: float | None = None,
    ) -> dict[str, Any]:
        return self.pipeline.play_recent_audio(seconds, output_device=output_device, volume=volume)

    def start_playback_capture(self) -> dict[str, Any]:
        return self.pipeline.start_playback_capture()

    def stop_playback_capture(self, volume: float | None = None) -> dict[str, Any]:
        return self.pipeline.stop_playback_capture(volume=volume)

    def play_captured_audio(
        self,
        output_device: str | None = None,
        volume: float | None = None,
    ) -> dict[str, Any]:
        return self.pipeline.play_captured_audio(output_device=output_device, volume=volume)

    def play_uploaded_audio(
        self,
        payload: bytes,
        output_device: str | None = None,
        volume: float | None = None,
        loop: bool = False,
    ) -> dict[str, Any]:
        return self.pipeline.play_uploaded_audio(payload, output_device=output_device, volume=volume, loop=loop)

    def stop_playback(self) -> dict[str, Any]:
        return self.pipeline.stop_playback()

    def set_playback_volume(self, volume: float | None = None) -> dict[str, Any]:
        return self.pipeline.set_playback_volume(volume)

    def playback_status(self) -> dict[str, Any]:
        return self.pipeline.playback_status()

    def lullaby_status(self) -> dict[str, Any]:
        return self.pipeline.lullaby_status()

    def list_lullabies(self) -> list[dict[str, Any]]:
        return self.pipeline.list_lullabies()

    def save_lullaby(
        self,
        payload: bytes,
        title: str | None = None,
        volume: float | None = None,
    ) -> dict[str, Any]:
        return self.pipeline.save_lullaby(payload, title=title, volume=volume)

    def play_lullaby(
        self,
        track_id: str | None = None,
        volume: float | None = None,
        position_ms: float | None = None,
        loop: bool = True,
    ) -> dict[str, Any]:
        return self.pipeline.play_lullaby(track_id=track_id, volume=volume, position_ms=position_ms, loop=loop)

    def pause_lullaby(self) -> dict[str, Any]:
        return self.pipeline.pause_lullaby()

    def seek_lullaby(self, position_ms: float | None) -> dict[str, Any]:
        return self.pipeline.seek_lullaby(position_ms)

    def audio_rate_hz(self, now: float | None = None) -> float | None:
        return self.pipeline.audio_rate_hz(now=now)

    def start_talk_input(self) -> dict[str, Any]:
        return self.pipeline.start_talk_input()

    def stop_talk_input(self) -> dict[str, Any]:
        return self.pipeline.stop_talk_input()

    def receive_talk_pcm(self, payload: bytes) -> dict[str, Any]:
        return self.pipeline.receive_talk_pcm(payload)

    def talk_input_status(self) -> dict[str, Any]:
        return self.pipeline.talk_input_status()

    def ensure_live_audio_capture(self) -> dict[str, Any]:
        return self.pipeline.ensure_live_audio_capture()

    def live_audio_position(self) -> int:
        return self.pipeline.live_audio_position()

    def read_live_pcm_since(self, position: int, max_bytes: int, timeout: float = 1.0) -> tuple[bytes, int]:
        return self.pipeline.read_live_pcm_since(position, max_bytes=max_bytes, timeout=timeout)


class HardwareRuntime:
    def __init__(self, config: EdgeConfig) -> None:
        hardware = config.hardware
        # Fill light = mirrored PWM4 (header pin 32) + PWM7 (header pin 40),
        # driven by pwm-control.service; this process only writes commands.
        self.light = DualPwmClient()
        self.temperature_humidity = TemperatureHumidityController(
            gpio=hardware.temperature_humidity_gpio,
            chip=hardware.temperature_humidity_chip,
            interval_ms=hardware.temperature_humidity_interval_ms,
            pull_up=hardware.temperature_humidity_pull_up,
        )

    def close(self) -> None:
        self.light.close()
        self.temperature_humidity.close()

    def day_night_status(self) -> dict[str, Any]:
        return day_night_status()

    def status(self) -> dict[str, Any]:
        return {
            "day_night": self.day_night_status(),
            "light": self.light.status(),
            "temperature_humidity": self.temperature_humidity.status(),
        }

    def set_light_duty(self, duty: float, frequency_hz: Any = None) -> dict[str, Any]:
        return self.light.set_duty(duty, frequency_hz=frequency_hz)


class DatasetRuntime:
    def __init__(self, config: EdgeConfig, video: VideoRuntime, hardware: HardwareRuntime) -> None:
        self.service = DatasetCaptureService(enabled=config.dataset.enable_dataset_capture)
        self._config = config
        self._video = video
        self._hardware = hardware

    def status(self) -> dict[str, Any]:
        return self.service.status()

    def stats(self) -> dict[str, Any]:
        return {**self.service.status(), "day_night": self._hardware.day_night_status()}

    def capture_pending(self, labels: dict[str, Any] | None = None) -> dict[str, Any]:
        day_night = self._hardware.day_night_status()
        metadata = {
            "created_at": time.time(),
            "labels": normalize_dataset_labels(labels, day_night),
            "day_night": day_night,
            "camera": self._video.camera.status(),
            "vision": self._video.vision.latest_result(),
            "config": self._config.public_config(),
        }
        return self.service.capture_pending(self._video.vision.latest_raw_jpeg(), metadata)

    def confirm(self, sample_id: str) -> dict[str, Any]:
        return self.service.confirm(sample_id)

    def cancel(self, sample_id: str) -> dict[str, Any]:
        return self.service.cancel(sample_id)


class StatusAggregator:
    def __init__(
        self,
        config: EdgeConfig,
        video: VideoRuntime,
        audio: AudioRuntime,
        hardware: HardwareRuntime,
        dataset: DatasetRuntime,
        resources: ResourceMonitor,
        activity_zone: ActivityZoneStore,
        started_at: float,
    ) -> None:
        self.config = config
        self.video = video
        self.audio = audio
        self.hardware = hardware
        self.dataset = dataset
        self.resources = resources
        self.activity_zone = activity_zone
        self.started_at = started_at

    def status(self) -> dict[str, Any]:
        now = time.time()
        video_status = self.video.status()
        vision = video_status["vision"]
        camera = video_status["camera"]
        yamnet = self.audio.latest_yamnet
        hardware = self.hardware.status()
        activity_zone = safe_zone_detection_status(self.activity_zone.status(), vision.get("latest"))
        vision_safety = vision_safety_status(vision.get("latest"))
        vision_safety["safe_zone_triggered"] = activity_zone["triggered"]
        return {
            "ok": True,
            "uptime_s": round(now - self.started_at, 1),
            "camera": camera,
            "camera_watchdog": video_status.get("camera_watchdog"),
            "vision": vision,
            "vision_safety": vision_safety,
            "resources": self.resources.snapshot(),
            "audio": self.audio.latest_audio,
            "audio_playback": self.audio.playback_status(),
            "lullaby": self.audio.lullaby_status(),
            "voice_trigger": read_voice_trigger_status(),
            "yamnet": yamnet,
            "day_night": hardware["day_night"],
            "light": hardware["light"],
            "temperature_humidity": hardware["temperature_humidity"],
            "activity_zone": activity_zone,
            "dataset": self.dataset.status(),
            "summary": self._summary(vision, camera, yamnet, now),
            "config": self.config.public_config(),
        }

    def healthz(self) -> dict[str, Any]:
        video = self.video.status()
        hardware = self.hardware.status()
        return {
            "ok": True,
            "service": "deepzzz-k1-edge",
            "uptime_s": round(time.time() - self.started_at, 1),
            "runtime": {
                "camera_running": video["camera"].get("running"),
                "vision_running": video["vision"].get("running"),
                "yamnet_status": (self.audio.latest_yamnet or {}).get("status"),
                "audio_playback_running": self.audio.playback_status().get("running"),
                "voice_trigger_state": read_voice_trigger_status().get("state"),
                "light_connected": hardware["light"].get("connected"),
                "temperature_humidity_connected": hardware["temperature_humidity"].get("connected"),
                "day_night_source": hardware["day_night"].get("source"),
                "dataset_enabled": self.dataset.status().get("enabled"),
            },
            "config": self.config.health_config(),
        }

    def _summary(self, vision: dict[str, Any], camera: dict[str, Any], yamnet: dict[str, Any] | None, now: float) -> dict[str, Any]:
        latest = vision.get("latest") or {}
        video_updated_at = latest.get("updated_at")
        camera_updated_at = camera.get("preview_updated_at")
        audio_updated_at = yamnet.get("updated_at") if yamnet else None
        return {
            "video_age_s": age_seconds(camera_updated_at or video_updated_at, now),
            "video_fps": camera.get("preview_actual_fps"),
            "analysis_fps": vision.get("actual_fps"),
            "audio_age_s": age_seconds(audio_updated_at, now),
            "audio_rate_hz": self.audio.audio_rate_hz(now=now),
            "npu_percent": estimated_npu_percent(vision),
        }


class AppRuntime:
    def __init__(self, config: EdgeConfig) -> None:
        self.config = config
        self.video = VideoRuntime(config)
        self.audio = AudioRuntime(config)
        self.hardware = HardwareRuntime(config)
        self.dataset = DatasetRuntime(config, self.video, self.hardware)
        self.resources = ResourceMonitor()
        self.activity_zone_store = ActivityZoneStore(ACTIVITY_ZONE_FILE)
        self.care = CareStore(CARE_RECORDS_FILE, CARE_PROFILE_FILE)
        self.assistant = ChatAssistant(
            load_assistant_settings(),
            overview=self.care.overview,
            room=self.hardware.temperature_humidity.status,
            pauser=WorkloadPauser(vision=self.video.vision),
        )
        self.started_at = time.time()
        self.status_aggregator = StatusAggregator(
            config=config,
            video=self.video,
            audio=self.audio,
            hardware=self.hardware,
            dataset=self.dataset,
            resources=self.resources,
            activity_zone=self.activity_zone_store,
            started_at=self.started_at,
        )

    @property
    def camera(self):
        return self.video.camera

    @property
    def vision(self):
        return self.video.vision

    def start(self) -> None:
        self.video.start()
        self.audio.start()

    def stop(self) -> None:
        self.hardware.close()
        self.audio.stop()
        self.video.stop()

    def status(self) -> dict[str, Any]:
        return self.status_aggregator.status()

    def healthz(self) -> dict[str, Any]:
        return self.status_aggregator.healthz()

    def alerts(self) -> list[dict[str, Any]]:
        status = self.status()
        now = time.time()
        alerts: list[dict[str, Any]] = []
        yamnet = status.get("yamnet") or {}
        if isinstance(yamnet, dict) and yamnet.get("crying") is True:
            updated_at = float(yamnet.get("updated_at") or now)
            score = float(yamnet.get("cry_score") or 0.0)
            alerts.append(
                {
                    "id": f"cry-{int(updated_at // 10)}",
                    "type": "cry",
                    "timestampMillis": int(updated_at * 1000),
                    "score": round(score, 4),
                    "noiseDb": yamnet.get("noise_db"),
                    "source": "yamnet",
                    "title": "哭声警告",
                }
            )
        vision_safety = status.get("vision_safety") or {}
        if isinstance(vision_safety, dict) and vision_safety.get("no_person") is True:
            updated_at = float(vision_safety.get("updated_at") or vision_safety.get("captured_at") or now)
            alerts.append(
                {
                    "id": f"no-person-{int(updated_at // 10)}",
                    "type": "no_person",
                    "timestampMillis": int(updated_at * 1000),
                    "source": "vision",
                    "title": "无人检测警告",
                    "status": vision_safety.get("status"),
                    "reason": vision_safety.get("reason"),
                }
            )
        if isinstance(vision_safety, dict) and vision_safety.get("face_cover_rollover") is True:
            updated_at = float(vision_safety.get("updated_at") or vision_safety.get("captured_at") or now)
            alerts.append(
                {
                    "id": f"face-{int(updated_at // 10)}",
                    "type": "face_cover_rollover",
                    "timestampMillis": int(updated_at * 1000),
                    "source": "vision",
                    "title": "遮脸/翻身警告",
                    "status": vision_safety.get("status"),
                    "reason": vision_safety.get("reason"),
                }
            )
        if isinstance(vision_safety, dict) and vision_safety.get("safe_zone_triggered") is True:
            updated_at = float(vision_safety.get("updated_at") or vision_safety.get("captured_at") or now)
            alerts.append(
                {
                    "id": f"safe-zone-{int(updated_at // 10)}",
                    "type": "boundary",
                    "timestampMillis": int(updated_at * 1000),
                    "source": "vision",
                    "title": "安全区域警告",
                    "status": "Outside safe zone",
                    "reason": "A body keypoint is outside the safe zone",
                }
            )
        alerts.sort(key=lambda item: item.get("timestampMillis") or 0, reverse=True)
        return alerts

    def vision_safety(self) -> dict[str, Any]:
        return vision_safety_status(self.vision.latest_result())

    def activity_zone(self) -> dict[str, Any]:
        return safe_zone_detection_status(self.activity_zone_store.status(), self.vision.latest_result())

    def set_activity_zone(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.activity_zone_store.update(payload)

    def clear_activity_zone(self) -> dict[str, Any]:
        return self.activity_zone_store.clear()

    def record_audio(self, seconds: float) -> dict[str, Any]:
        return self.audio.record_audio(seconds)

    def analyze_cry(self, seconds: float) -> dict[str, Any]:
        return self.audio.analyze_cry(seconds)

    def play_recent_audio(
        self,
        seconds: float,
        output_device: str | None = None,
        volume: float | None = None,
    ) -> dict[str, Any]:
        return self.audio.play_recent_audio(seconds, output_device=output_device, volume=volume)

    def start_playback_capture(self) -> dict[str, Any]:
        return self.audio.start_playback_capture()

    def stop_playback_capture(self, volume: float | None = None) -> dict[str, Any]:
        return self.audio.stop_playback_capture(volume=volume)

    def play_captured_audio(
        self,
        output_device: str | None = None,
        volume: float | None = None,
    ) -> dict[str, Any]:
        return self.audio.play_captured_audio(output_device=output_device, volume=volume)

    def play_uploaded_audio(
        self,
        payload: bytes,
        output_device: str | None = None,
        volume: float | None = None,
        loop: bool = False,
    ) -> dict[str, Any]:
        return self.audio.play_uploaded_audio(payload, output_device=output_device, volume=volume, loop=loop)

    def stop_playback(self) -> dict[str, Any]:
        return self.audio.stop_playback()

    def set_playback_volume(self, volume: float | None = None) -> dict[str, Any]:
        return self.audio.set_playback_volume(volume)

    def playback_status(self) -> dict[str, Any]:
        return self.audio.playback_status()

    def lullaby_status(self) -> dict[str, Any]:
        return self.audio.lullaby_status()

    def voice_trigger_status(self) -> dict[str, Any]:
        return read_voice_trigger_status()

    def set_voice_trigger_enabled(self, enabled: bool) -> dict[str, Any]:
        write_voice_trigger_command(enabled)
        return read_voice_trigger_status()

    def list_lullabies(self) -> list[dict[str, Any]]:
        return self.audio.list_lullabies()

    def save_lullaby(
        self,
        payload: bytes,
        title: str | None = None,
        volume: float | None = None,
    ) -> dict[str, Any]:
        return self.audio.save_lullaby(payload, title=title, volume=volume)

    def play_lullaby(
        self,
        track_id: str | None = None,
        volume: float | None = None,
        position_ms: float | None = None,
        loop: bool = True,
    ) -> dict[str, Any]:
        return self.audio.play_lullaby(track_id=track_id, volume=volume, position_ms=position_ms, loop=loop)

    def pause_lullaby(self) -> dict[str, Any]:
        return self.audio.pause_lullaby()

    def seek_lullaby(self, position_ms: float | None) -> dict[str, Any]:
        return self.audio.seek_lullaby(position_ms)

    def start_talk_input(self) -> dict[str, Any]:
        return self.audio.start_talk_input()

    def stop_talk_input(self) -> dict[str, Any]:
        return self.audio.stop_talk_input()

    def receive_talk_pcm(self, payload: bytes) -> dict[str, Any]:
        return self.audio.receive_talk_pcm(payload)

    def talk_input_status(self) -> dict[str, Any]:
        return self.audio.talk_input_status()

    def ensure_live_audio_capture(self) -> dict[str, Any]:
        return self.audio.ensure_live_audio_capture()

    def live_audio_position(self) -> int:
        return self.audio.live_audio_position()

    def read_live_pcm_since(self, position: int, max_bytes: int, timeout: float = 1.0) -> tuple[bytes, int]:
        return self.audio.read_live_pcm_since(position, max_bytes=max_bytes, timeout=timeout)

    def set_light_duty(self, duty: float, frequency_hz: Any = None) -> dict[str, Any]:
        return self.hardware.set_light_duty(duty, frequency_hz=frequency_hz)

    def capture_dataset_sample(self, labels: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.dataset.capture_pending(labels)

    def delete_dataset_sample(self, sample_id: str) -> dict[str, Any]:
        return self.dataset.cancel(sample_id)

    def confirm_dataset_sample(self, sample_id: str) -> dict[str, Any]:
        return self.dataset.confirm(sample_id)

    def dataset_stats(self) -> dict[str, Any]:
        return self.dataset.stats()


def estimated_npu_percent(vision: dict[str, Any]) -> float | None:
    provider = str(vision.get("provider") or "")
    if provider != "SpaceMITExecutionProvider":
        return None
    latest = vision.get("latest") or {}
    elapsed_ms = latest.get("elapsed_ms")
    fps = vision.get("actual_fps")
    try:
        busy = float(elapsed_ms) * float(fps) / 10.0
    except (TypeError, ValueError):
        return None
    return round(max(0.0, min(100.0, busy)), 1)


def normalize_dataset_labels(labels: dict[str, Any] | None, day_night: dict[str, Any]) -> dict[str, str]:
    light = str((labels or {}).get("light") or "").strip().lower()
    if light not in {"day", "night_ir", "low_light"}:
        light = "night_ir" if day_night.get("state") == "night" else "day"
    return {"light": light}
