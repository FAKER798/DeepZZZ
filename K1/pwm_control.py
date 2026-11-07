#!/usr/bin/env python3
"""Entry point for the mirrored PWM4 + PWM7 worker (pwm-control.service).

No network port: the 7860 main service controls it through
runtime/state/pwm_dual_command.json (see devices/pwm_dual.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from devices.pwm_dual import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
