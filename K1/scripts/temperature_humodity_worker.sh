#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_ROOT}"
exec python3 devices/temperature_humidity.py --gpio 92 --chip gpiochip0 --interval-ms 2500 --pull-up --attempts 3 --retry-ms 2300 "$@"
