from __future__ import annotations

from contextlib import ExitStack
import subprocess
import sys
import time
from pathlib import Path

from devices.ircut import DEFAULT_IN1, DEFAULT_IN2, DEFAULT_PULSE_MS, DEFAULT_SETTLE_MS
from devices.v4l2_controls import (
    DEFAULT_DAY_SATURATION,
    DEFAULT_DEVICE,
    DEFAULT_NIGHT_SATURATION,
    sync_day_night_saturation,
)
from k1edge.runtime_store import write_json
from utils.paths import DAY_NIGHT_STATE_FILE, IRCUT_SCRIPT


DEFAULT_GPIO = 35
DEFAULT_INTERVAL_MS = 100


def resolve_pull_mode(pull_up: bool, floating: bool):
    if pull_up:
        return True
    if floating:
        return None
    return False


def resolve_label(value: int, active_low: bool) -> str:
    logical_high = bool(value) if active_low else not value
    return "day" if logical_high else "night"


def sync_ircut(
    state: str,
    ir_cut_script: str | Path = IRCUT_SCRIPT,
    in1: int = DEFAULT_IN1,
    in2: int = DEFAULT_IN2,
    pulse_ms: int = DEFAULT_PULSE_MS,
    settle_ms: int = DEFAULT_SETTLE_MS,
    ir_active_low: bool = False,
) -> None:
    command = [
        sys.executable,
        str(Path(ir_cut_script)),
        state,
        "--in1",
        str(in1),
        "--in2",
        str(in2),
        "--pulse-ms",
        str(pulse_ms),
        "--settle-ms",
        str(settle_ms),
    ]
    command.append("--active-low" if ir_active_low else "--active-high")
    subprocess.run(command, check=True)


def write_day_night_state(state: str) -> None:
    write_json(DAY_NIGHT_STATE_FILE, {"state": state, "updated_at": time.time()})


def watch_day_night(
    action: str,
    gpio: int = DEFAULT_GPIO,
    interval_ms: int = DEFAULT_INTERVAL_MS,
    debounce_ms: int = 20,
    active_low: bool = False,
    pull_up: bool = False,
    floating: bool = False,
    ir_cut_script: str | Path = IRCUT_SCRIPT,
    in1: int = DEFAULT_IN1,
    in2: int = DEFAULT_IN2,
    pulse_ms: int = DEFAULT_PULSE_MS,
    settle_ms: int = DEFAULT_SETTLE_MS,
    ir_active_low: bool = False,
    sync_camera: bool = False,
    camera_device: str = DEFAULT_DEVICE,
    day_saturation: int = DEFAULT_DAY_SATURATION,
    night_saturation: int = DEFAULT_NIGHT_SATURATION,
    camera_delay_ms: int = 0,
) -> int:
    from gpiozero import Device, DigitalInputDevice
    from gpiozero.pins.lgpio import LGPIOFactory

    Device.pin_factory = LGPIOFactory()
    bounce_time = max(debounce_ms, 0) / 1000.0 if debounce_ms is not None else None
    interval_s = max(interval_ms, 1) / 1000.0

    with ExitStack() as stack:
        sensor = stack.enter_context(
            DigitalInputDevice(
                gpio,
                pull_up=resolve_pull_mode(pull_up, floating),
                bounce_time=bounce_time,
            )
        )

        if action == "status":
            current_label = resolve_label(sensor.value, active_low)
            write_day_night_state(current_label)
            print(current_label)
            return 0

        last_label = None
        while True:
            current_label = resolve_label(sensor.value, active_low)
            if current_label != last_label:
                print(current_label, flush=True)
                if action == "sync-ircut":
                    sync_ircut(
                        current_label,
                        ir_cut_script=ir_cut_script,
                        in1=in1,
                        in2=in2,
                        pulse_ms=pulse_ms,
                        settle_ms=settle_ms,
                        ir_active_low=ir_active_low,
                    )
                if sync_camera:
                    delay_s = max(camera_delay_ms, 0) / 1000.0
                    if delay_s:
                        print(f"waiting {camera_delay_ms}ms before camera saturation sync", flush=True)
                        time.sleep(delay_s)
                    sync_day_night_saturation(
                        current_label,
                        device=camera_device,
                        day_saturation=day_saturation,
                        night_saturation=night_saturation,
                    )
                    print(f"sync complete: ircut={current_label} camera={current_label}", flush=True)
                write_day_night_state(current_label)
                last_label = current_label
            time.sleep(interval_s)
