from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, TypeVar

from utils.paths import PROJECT_ROOT


T = TypeVar("T")

CONFIG_PATH = PROJECT_ROOT / "config.json"

DEFAULTS: dict[str, Any] = {
    "host": "0.0.0.0",
    "port": 7860,
    "dataset_capture_port": 7861,
    "camera_device": "auto",
    "preview_size": "1280x720",
    "preview_fps": 30,
    "analysis_width": 320,
    "analysis_fps": 0.0,
    "audio_device": "plughw:CARD=UACDemoV10,DEV=0",
    "audio_output_device": "plughw:CARD=UACDemoV10,DEV=0",
    "audio_playback_volume": 1.0,
    "audio_rate": 16000,
    "yamnet_model": "models/yamnet/yamnet.onnx",
    "yamnet_labels": "models/yamnet/yamnet_class_map.csv",
    "enable_yamnet": True,
    "yamnet_seconds": 2.0,
    "yamnet_interval": 0.5,
    "pose_model": "/home/z/.brdk_models/pose/yolov8n-pose-k1-320-int8.onnx",
    "pose_provider": "SpaceMITExecutionProvider",
    "pose_provider_fallback": False,
    "low_light_mode": "off",
    "low_light_gamma": 1.35,
    "low_light_clahe_clip": 2.0,
    "low_light_desaturate": False,
    "enable_dataset_capture": False,
    "enable_pose": False,
    "start_camera": True,
    "light_gpio": 74,
    "light_frequency_hz": 10_000,
    "light_pwmchip": "auto",
    "light_pwm_device": "d401b000.pwm",
    "light_pwm_channel": 0,
    "light_active_low": False,
    "temperature_humidity_gpio": 92,
    "temperature_humidity_chip": "gpiochip0",
    "temperature_humidity_interval_ms": 2500,
    "temperature_humidity_pull_up": True,
}

ENV_KEYS = {
    "host": "HOST",
    "port": "PORT",
    "dataset_capture_port": "DATASET_CAPTURE_PORT",
    "camera_device": "CAMERA_DEVICE",
    "preview_size": "PREVIEW_SIZE",
    "preview_fps": "PREVIEW_FPS",
    "analysis_width": "ANALYSIS_WIDTH",
    "analysis_fps": "ANALYSIS_FPS",
    "audio_device": "AUDIO_DEVICE",
    "audio_output_device": "AUDIO_OUTPUT_DEVICE",
    "audio_playback_volume": "AUDIO_PLAYBACK_VOLUME",
    "audio_rate": "AUDIO_RATE",
    "yamnet_model": "YAMNET_MODEL",
    "yamnet_labels": "YAMNET_LABELS",
    "enable_yamnet": "ENABLE_YAMNET",
    "yamnet_seconds": "YAMNET_SECONDS",
    "yamnet_interval": "YAMNET_INTERVAL",
    "pose_model": "POSE_MODEL",
    "pose_provider": "POSE_PROVIDER",
    "pose_provider_fallback": "POSE_PROVIDER_FALLBACK",
    "low_light_mode": "LOW_LIGHT_MODE",
    "low_light_gamma": "LOW_LIGHT_GAMMA",
    "low_light_clahe_clip": "LOW_LIGHT_CLAHE_CLIP",
    "low_light_desaturate": "LOW_LIGHT_DESATURATE",
    "enable_dataset_capture": "ENABLE_DATASET_CAPTURE",
    "enable_pose": "ENABLE_POSE",
    "start_camera": "START_CAMERA",
    "light_gpio": "LIGHT_GPIO",
    "light_frequency_hz": "LIGHT_FREQUENCY_HZ",
    "light_pwmchip": "LIGHT_PWMCHIP",
    "light_pwm_device": "LIGHT_PWM_DEVICE",
    "light_pwm_channel": "LIGHT_PWM_CHANNEL",
    "light_active_low": "LIGHT_ACTIVE_LOW",
    "temperature_humidity_gpio": "TEMPERATURE_HUMIDITY_GPIO",
    "temperature_humidity_chip": "TEMPERATURE_HUMIDITY_CHIP",
    "temperature_humidity_interval_ms": "TEMPERATURE_HUMIDITY_INTERVAL_MS",
    "temperature_humidity_pull_up": "TEMPERATURE_HUMIDITY_PULL_UP",
}


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    dataset_capture_port: int


@dataclass(frozen=True)
class VideoConfig:
    camera_device: str
    preview_size: str
    preview_fps: int
    analysis_width: int
    analysis_fps: float
    start_camera: bool


@dataclass(frozen=True)
class AudioConfig:
    audio_device: str
    audio_output_device: str
    audio_playback_volume: float
    audio_rate: int
    enable_yamnet: bool
    yamnet_model: str
    yamnet_labels: str
    yamnet_seconds: float
    yamnet_interval: float


@dataclass(frozen=True)
class AiConfig:
    pose_model: str
    pose_provider: str
    pose_provider_fallback: bool
    enable_pose: bool
    low_light_mode: str
    low_light_gamma: float
    low_light_clahe_clip: float
    low_light_desaturate: bool


@dataclass(frozen=True)
class DatasetConfig:
    enable_dataset_capture: bool


@dataclass(frozen=True)
class HardwareConfig:
    light_gpio: int
    light_frequency_hz: int
    light_pwmchip: str
    light_pwm_device: str
    light_pwm_channel: int
    light_active_low: bool
    temperature_humidity_gpio: int
    temperature_humidity_chip: str
    temperature_humidity_interval_ms: int
    temperature_humidity_pull_up: bool


@dataclass(frozen=True)
class EdgeConfig:
    server: ServerConfig
    video: VideoConfig
    audio: AudioConfig
    ai: AiConfig
    dataset: DatasetConfig
    hardware: HardwareConfig
    values: dict[str, Any]

    def to_namespace(self) -> argparse.Namespace:
        values = dict(self.values)
        values["device"] = values["camera_device"]
        return argparse.Namespace(**values)

    def public_config(self) -> dict[str, Any]:
        return {
            "preview_size": self.video.preview_size,
            "preview_fps": self.video.preview_fps,
            "analysis_width": self.video.analysis_width,
            "analysis_fps": self.video.analysis_fps,
            "pose_model": self.ai.pose_model,
            "enable_pose": self.ai.enable_pose,
            "pose_provider": self.ai.pose_provider,
            "pose_provider_fallback": self.ai.pose_provider_fallback,
            "low_light_mode": self.ai.low_light_mode,
            "low_light_gamma": self.ai.low_light_gamma,
            "low_light_clahe_clip": self.ai.low_light_clahe_clip,
            "low_light_desaturate": self.ai.low_light_desaturate,
            "enable_dataset_capture": self.dataset.enable_dataset_capture,
            "dataset_capture_port": self.server.dataset_capture_port,
            "light_gpio": self.hardware.light_gpio,
            "light_frequency_hz": self.hardware.light_frequency_hz,
            "light_pwmchip": self.hardware.light_pwmchip,
            "light_pwm_device": self.hardware.light_pwm_device,
            "light_pwm_channel": self.hardware.light_pwm_channel,
            "light_active_low": self.hardware.light_active_low,
            "temperature_humidity_gpio": self.hardware.temperature_humidity_gpio,
            "temperature_humidity_chip": self.hardware.temperature_humidity_chip,
            "temperature_humidity_interval_ms": self.hardware.temperature_humidity_interval_ms,
            "temperature_humidity_pull_up": self.hardware.temperature_humidity_pull_up,
            "audio_device": self.audio.audio_device,
            "audio_output_device": self.audio.audio_output_device,
            "audio_playback_volume": self.audio.audio_playback_volume,
            "yamnet_model": self.audio.yamnet_model,
            "enable_yamnet": self.audio.enable_yamnet,
            "yamnet_seconds": self.audio.yamnet_seconds,
            "yamnet_interval": self.audio.yamnet_interval,
        }

    def health_config(self) -> dict[str, Any]:
        return {
            "server": asdict(self.server),
            "video": asdict(self.video),
            "audio": {
                **asdict(self.audio),
                "yamnet_model": self.audio.yamnet_model,
            },
            "ai": asdict(self.ai),
            "dataset": asdict(self.dataset),
            "hardware": asdict(self.hardware),
        }


def parse_config(argv: list[str] | None = None, config_path: Path = CONFIG_PATH) -> EdgeConfig:
    values = resolved_defaults(load_config(config_path))
    args = build_parser(values).parse_args(argv)
    resolved = namespace_to_values(args)
    return build_config(resolved)


def build_parser(values: dict[str, Any]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DeepZZZ K1 edge service")
    parser.add_argument("--host", default=values["host"])
    parser.add_argument("--port", type=int, default=values["port"])
    parser.add_argument("--dataset-capture-port", type=int, default=values["dataset_capture_port"])
    parser.add_argument("--device", dest="camera_device", default=values["camera_device"])
    parser.add_argument("--preview-size", default=values["preview_size"])
    parser.add_argument("--preview-fps", type=int, default=values["preview_fps"])
    parser.add_argument("--analysis-width", type=int, default=values["analysis_width"])
    parser.add_argument("--analysis-fps", type=float, default=values["analysis_fps"])
    parser.add_argument("--audio-device", default=values["audio_device"])
    parser.add_argument("--audio-output-device", default=values["audio_output_device"])
    parser.add_argument("--audio-playback-volume", type=float, default=values["audio_playback_volume"])
    parser.add_argument("--audio-rate", type=int, default=values["audio_rate"])
    parser.add_argument("--yamnet-model", default=values["yamnet_model"])
    parser.add_argument("--yamnet-labels", default=values["yamnet_labels"])
    parser.add_argument("--enable-yamnet", dest="enable_yamnet", action="store_true", default=values["enable_yamnet"])
    parser.add_argument("--disable-yamnet", dest="enable_yamnet", action="store_false")
    parser.add_argument("--yamnet-seconds", type=float, default=values["yamnet_seconds"])
    parser.add_argument("--yamnet-interval", type=float, default=values["yamnet_interval"])
    parser.add_argument("--pose-model", default=values["pose_model"])
    parser.add_argument("--pose-provider", default=values["pose_provider"])
    parser.add_argument("--pose-provider-fallback", dest="pose_provider_fallback", action="store_true", default=values["pose_provider_fallback"])
    parser.add_argument("--no-pose-provider-fallback", dest="pose_provider_fallback", action="store_false")
    parser.add_argument("--low-light-mode", choices=["off", "auto", "night", "on"], default=values["low_light_mode"])
    parser.add_argument("--low-light-gamma", type=float, default=values["low_light_gamma"])
    parser.add_argument("--low-light-clahe-clip", type=float, default=values["low_light_clahe_clip"])
    parser.add_argument("--low-light-desaturate", dest="low_light_desaturate", action="store_true", default=values["low_light_desaturate"])
    parser.add_argument("--no-low-light-desaturate", dest="low_light_desaturate", action="store_false")
    parser.add_argument("--enable-dataset-capture", dest="enable_dataset_capture", action="store_true", default=values["enable_dataset_capture"])
    parser.add_argument("--disable-dataset-capture", dest="enable_dataset_capture", action="store_false")
    parser.add_argument("--enable-pose", dest="enable_pose", action="store_true", default=values["enable_pose"])
    parser.add_argument("--disable-pose", dest="enable_pose", action="store_false")
    parser.add_argument("--no-camera", dest="start_camera", action="store_false", default=values["start_camera"])
    parser.add_argument("--camera", dest="start_camera", action="store_true")
    parser.add_argument("--light-gpio", type=int, default=values["light_gpio"])
    parser.add_argument("--light-frequency-hz", type=int, default=values["light_frequency_hz"])
    parser.add_argument("--light-pwmchip", default=values["light_pwmchip"])
    parser.add_argument("--light-pwm-device", default=values["light_pwm_device"])
    parser.add_argument("--light-pwm-channel", type=int, default=values["light_pwm_channel"])
    parser.add_argument("--light-active-low", dest="light_active_low", action="store_true", default=values["light_active_low"])
    parser.add_argument("--light-active-high", dest="light_active_low", action="store_false")
    parser.add_argument("--temperature-humidity-gpio", type=int, default=values["temperature_humidity_gpio"])
    parser.add_argument("--temperature-humidity-chip", default=values["temperature_humidity_chip"])
    parser.add_argument("--temperature-humidity-interval-ms", type=int, default=values["temperature_humidity_interval_ms"])
    parser.add_argument("--temperature-humidity-pull-up", dest="temperature_humidity_pull_up", action="store_true", default=values["temperature_humidity_pull_up"])
    parser.add_argument("--temperature-humidity-no-pull-up", dest="temperature_humidity_pull_up", action="store_false")
    return parser


def load_config(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid config file {path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def resolved_defaults(file_config: dict[str, Any]) -> dict[str, Any]:
    values = DEFAULTS.copy()
    values.update({key: value for key, value in file_config.items() if key in DEFAULTS})
    for key, env_key in ENV_KEYS.items():
        if env_key not in os.environ:
            continue
        default = DEFAULTS[key]
        values[key] = cast_value(os.environ[env_key], type(default))
    return values


def namespace_to_values(namespace: argparse.Namespace | SimpleNamespace) -> dict[str, Any]:
    values = vars(namespace).copy()
    if "device" in values and "camera_device" not in values:
        values["camera_device"] = values["device"]
    return {key: values[key] for key in DEFAULTS if key in values}


def build_config(values: dict[str, Any]) -> EdgeConfig:
    merged = DEFAULTS.copy()
    merged.update({key: values[key] for key in DEFAULTS if key in values})
    return EdgeConfig(
        server=ServerConfig(
            host=str(merged["host"]),
            port=int(merged["port"]),
            dataset_capture_port=int(merged["dataset_capture_port"]),
        ),
        video=VideoConfig(
            camera_device=str(merged["camera_device"]),
            preview_size=str(merged["preview_size"]),
            preview_fps=int(merged["preview_fps"]),
            analysis_width=int(merged["analysis_width"]),
            analysis_fps=float(merged["analysis_fps"]),
            start_camera=bool(merged["start_camera"]),
        ),
        audio=AudioConfig(
            audio_device=str(merged["audio_device"]),
            audio_output_device=str(merged["audio_output_device"]),
            audio_playback_volume=float(merged["audio_playback_volume"]),
            audio_rate=int(merged["audio_rate"]),
            enable_yamnet=bool(merged["enable_yamnet"]),
            yamnet_model=str(merged["yamnet_model"]),
            yamnet_labels=str(merged["yamnet_labels"]),
            yamnet_seconds=float(merged["yamnet_seconds"]),
            yamnet_interval=float(merged["yamnet_interval"]),
        ),
        ai=AiConfig(
            pose_model=str(merged["pose_model"]),
            pose_provider=str(merged["pose_provider"]),
            pose_provider_fallback=bool(merged["pose_provider_fallback"]),
            enable_pose=bool(merged["enable_pose"]),
            low_light_mode=str(merged["low_light_mode"]),
            low_light_gamma=float(merged["low_light_gamma"]),
            low_light_clahe_clip=float(merged["low_light_clahe_clip"]),
            low_light_desaturate=bool(merged["low_light_desaturate"]),
        ),
        dataset=DatasetConfig(enable_dataset_capture=bool(merged["enable_dataset_capture"])),
        hardware=HardwareConfig(
            light_gpio=int(merged["light_gpio"]),
            light_frequency_hz=int(merged["light_frequency_hz"]),
            light_pwmchip=str(merged["light_pwmchip"]),
            light_pwm_device=str(merged["light_pwm_device"]),
            light_pwm_channel=int(merged["light_pwm_channel"]),
            light_active_low=bool(merged["light_active_low"]),
            temperature_humidity_gpio=int(merged["temperature_humidity_gpio"]),
            temperature_humidity_chip=str(merged["temperature_humidity_chip"]),
            temperature_humidity_interval_ms=int(merged["temperature_humidity_interval_ms"]),
            temperature_humidity_pull_up=bool(merged["temperature_humidity_pull_up"]),
        ),
        values=merged,
    )


def cast_value(value: Any, target: Callable[[Any], T]) -> T:
    if target is bool:
        return parse_bool(value)  # type: ignore[return-value]
    return target(value)


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
