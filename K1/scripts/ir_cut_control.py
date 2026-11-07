#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from devices.ircut import DEFAULT_IN1, DEFAULT_IN2, DEFAULT_PULSE_MS, DEFAULT_SETTLE_MS, run_ircut_action  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="IR-CUT H-bridge controller for SpaceMIT K1 / MUSE Pi Pro")
    parser.add_argument("action", choices=["day", "night", "off", "status", "pulse-a", "pulse-b"])
    parser.add_argument("--in1", type=int, default=DEFAULT_IN1, help="H-bridge input A GPIO number")
    parser.add_argument("--in2", type=int, default=DEFAULT_IN2, help="H-bridge input B GPIO number")
    parser.add_argument("--pulse-ms", type=int, default=DEFAULT_PULSE_MS, help="drive pulse width in milliseconds")
    parser.add_argument("--settle-ms", type=int, default=DEFAULT_SETTLE_MS, help="delay after releasing both lines")
    parser.add_argument("--active-high", action="store_true", default=True, help="drive H bridge with active-high outputs")
    parser.add_argument("--active-low", dest="active_high", action="store_false", help="invert output polarity")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run_ircut_action(
        args.action,
        in1_gpio=args.in1,
        in2_gpio=args.in2,
        pulse_ms=args.pulse_ms,
        settle_ms=args.settle_ms,
        active_high=args.active_high,
    )


if __name__ == "__main__":
    raise SystemExit(main())
