from __future__ import annotations

from typing import Literal

import cv2
import numpy as np


LowLightMode = Literal["off", "auto", "night", "on"]

DEFAULT_BRIGHTNESS_THRESHOLD = 70.0


def normalize_low_light_mode(value: str | None) -> LowLightMode:
    mode = (value or "off").strip().lower()
    if mode in {"0", "false", "no", "disabled", "disable"}:
        return "off"
    if mode in {"1", "true", "yes", "enabled", "enable"}:
        return "on"
    if mode in {"off", "auto", "night", "on"}:
        return mode  # type: ignore[return-value]
    return "off"


def luminance_mean(frame_bgr: np.ndarray) -> float:
    if frame_bgr.size == 0:
        return 0.0
    y_channel = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2YCrCb)[:, :, 0]
    return float(np.mean(y_channel))


def should_enhance_low_light(
    frame_bgr: np.ndarray,
    mode: str | None,
    day_night_state: str | None,
    brightness_threshold: float = DEFAULT_BRIGHTNESS_THRESHOLD,
) -> tuple[bool, float, LowLightMode]:
    normalized = normalize_low_light_mode(mode)
    brightness = luminance_mean(frame_bgr)
    if normalized == "off":
        return False, brightness, normalized
    if normalized == "on":
        return True, brightness, normalized
    if normalized == "night":
        return day_night_state == "night", brightness, normalized
    return day_night_state == "night" or brightness < brightness_threshold, brightness, normalized


def enhance_low_light(
    frame_bgr: np.ndarray,
    gamma: float = 1.35,
    clahe_clip_limit: float = 2.0,
    desaturate: bool = True,
) -> np.ndarray:
    gamma = max(0.1, float(gamma))
    clahe_clip_limit = max(0.1, float(clahe_clip_limit))

    if desaturate:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        frame_bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    lab = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB)
    lightness, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clahe_clip_limit, tileGridSize=(8, 8))
    lightness = clahe.apply(lightness)
    enhanced = cv2.cvtColor(cv2.merge((lightness, a_channel, b_channel)), cv2.COLOR_LAB2BGR)

    if abs(gamma - 1.0) < 1e-3:
        return enhanced
    lookup = np.array([((value / 255.0) ** (1.0 / gamma)) * 255 for value in range(256)], dtype=np.uint8)
    return cv2.LUT(enhanced, lookup)
