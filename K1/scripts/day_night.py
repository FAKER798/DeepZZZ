#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from devices.day_night_gpio import DEFAULT_GPIO, DEFAULT_INTERVAL_MS, watch_day_night  # noqa: E402
from devices.ircut import DEFAULT_IN1, DEFAULT_IN2, DEFAULT_PULSE_MS, DEFAULT_SETTLE_MS  # noqa: E402
from devices.v4l2_controls import DEFAULT_DAY_SATURATION, DEFAULT_DEVICE, DEFAULT_NIGHT_SATURATION  # noqa: E402
from utils.paths import IRCUT_SCRIPT  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Print "day" on high level and "night" on low level for a K1 GPIO input.'
    )
    parser.add_argument(
        "action",
        choices=["status", "watch", "sync-ircut"],
        nargs="?",
        default="status",
        help="status: print current state once; watch: print changes; sync-ircut: also drive IR-CUT",
    )
    parser.add_argument("--gpio", type=int, default=DEFAULT_GPIO, help="GPIO number to read")
    parser.add_argument("--interval-ms", type=int, default=DEFAULT_INTERVAL_MS, help="poll interval in milliseconds")
    parser.add_argument("--debounce-ms", type=int, default=20, help="debounce time in milliseconds")
    parser.add_argument("--active-low", action="store_true", help="invert the input logic")

    pull = parser.add_mutually_exclusive_group()
    pull.add_argument("--pull-up", action="store_true", help="enable the internal pull-up resistor")
    pull.add_argument("--floating", action="store_true", help="do not enable an internal pull resistor")

    parser.add_argument("--ir-cut-script", default=str(IRCUT_SCRIPT), help="path to ir_cut_control.py")
    parser.add_argument("--in1", type=int, default=DEFAULT_IN1, help="IR-CUT H-bridge input A GPIO number")
    parser.add_argument("--in2", type=int, default=DEFAULT_IN2, help="IR-CUT H-bridge input B GPIO number")
    parser.add_argument("--pulse-ms", type=int, default=DEFAULT_PULSE_MS, help="IR-CUT drive pulse width")
    parser.add_argument("--settle-ms", type=int, default=DEFAULT_SETTLE_MS, help="IR-CUT settle delay")
    parser.add_argument("--ir-active-low", action="store_true", help="drive IR-CUT H-bridge with active-low outputs")
    parser.add_argument("--sync-camera", action="store_true", help="also switch camera saturation for day/night")
    parser.add_argument("--camera-device", default=DEFAULT_DEVICE, help="V4L2 camera device for day/night controls")
    parser.add_argument("--day-saturation", type=int, default=DEFAULT_DAY_SATURATION)
    parser.add_argument("--night-saturation", type=int, default=DEFAULT_NIGHT_SATURATION)
    parser.add_argument("--camera-delay-ms", type=int, default=0, help="delay camera saturation after IR-CUT action")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return watch_day_night(
        args.action,
        gpio=args.gpio,
        interval_ms=args.interval_ms,
        debounce_ms=args.debounce_ms,
        active_low=args.active_low,
        pull_up=args.pull_up,
        floating=args.floating,
        ir_cut_script=args.ir_cut_script,
        in1=args.in1,
        in2=args.in2,
        pulse_ms=args.pulse_ms,
        settle_ms=args.settle_ms,
        ir_active_low=args.ir_active_low,
        sync_camera=args.sync_camera,
        camera_device=args.camera_device,
        day_saturation=args.day_saturation,
        night_saturation=args.night_saturation,
        camera_delay_ms=args.camera_delay_ms,
    )


if __name__ == "__main__":
    raise SystemExit(main())
