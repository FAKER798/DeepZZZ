from __future__ import annotations

import subprocess


DEFAULT_DEVICE = "/dev/v4l/by-id/usb-HDMI_3.0_USB_Camera_HDMI_USB_Camera_2023121018-video-index0"
DEFAULT_DAY_SATURATION = 100
DEFAULT_NIGHT_SATURATION = 0


def set_control(device: str, name: str, value: int) -> None:
    subprocess.run(
        ["v4l2-ctl", "-d", device, "-c", f"{name}={value}"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def sync_day_night_saturation(
    state: str,
    device: str = DEFAULT_DEVICE,
    day_saturation: int = DEFAULT_DAY_SATURATION,
    night_saturation: int = DEFAULT_NIGHT_SATURATION,
) -> None:
    saturation = night_saturation if state == "night" else day_saturation
    set_control(device, "saturation", saturation)
    print(f"camera saturation set to {saturation} for {state}", flush=True)
