#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_ROOT}"
exec python3 devices/lightcontrol.py --gpio 74 --frequency-hz 10000 --pwmchip auto --pwm-device d401b000.pwm --channel 0 --pinmux-address 0xd401e08c --pinmux-value 0x0000d043 --active-high "$@"
