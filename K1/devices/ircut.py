from __future__ import annotations

from contextlib import ExitStack
import time

from k1edge.runtime_store import write_json
from utils.paths import IRCUT_MODE_FILE


DEFAULT_IN1 = 33
DEFAULT_IN2 = 46
DEFAULT_PULSE_MS = 250
DEFAULT_SETTLE_MS = 50


def set_both(dev1, dev2, a: bool, b: bool) -> None:
    dev1.value = 1 if a else 0
    dev2.value = 1 if b else 0


def pulse(dev1, dev2, a: bool, b: bool, pulse_ms: int, settle_ms: int) -> None:
    set_both(dev1, dev2, False, False)
    time.sleep(max(settle_ms, 0) / 1000.0)
    set_both(dev1, dev2, a, b)
    time.sleep(max(pulse_ms, 0) / 1000.0)
    set_both(dev1, dev2, False, False)
    time.sleep(max(settle_ms, 0) / 1000.0)


def run_ircut_action(
    action: str,
    in1_gpio: int = DEFAULT_IN1,
    in2_gpio: int = DEFAULT_IN2,
    pulse_ms: int = DEFAULT_PULSE_MS,
    settle_ms: int = DEFAULT_SETTLE_MS,
    active_high: bool = True,
) -> int:
    from gpiozero import Device, OutputDevice
    from gpiozero.pins.lgpio import LGPIOFactory

    Device.pin_factory = LGPIOFactory()
    with ExitStack() as stack:
        in1 = stack.enter_context(OutputDevice(in1_gpio, active_high=active_high, initial_value=False))
        in2 = stack.enter_context(OutputDevice(in2_gpio, active_high=active_high, initial_value=False))

        if action == "status":
            print(f"IN1(GPIO{in1_gpio})={int(in1.value)} IN2(GPIO{in2_gpio})={int(in2.value)}")
            return 0
        if action == "off":
            set_both(in1, in2, False, False)
            write_ircut_mode("off")
            print(f"IR-CUT outputs released: IN1=0 IN2=0 on GPIO{in1_gpio}/GPIO{in2_gpio}")
            return 0
        if action in ("day", "pulse-a"):
            pulse(in1, in2, True, False, pulse_ms, settle_ms)
            write_ircut_mode("day")
            print(f"IR-CUT switched to DAY using GPIO{in1_gpio}=1 pulse, GPIO{in2_gpio}=0")
            return 0
        if action in ("night", "pulse-b"):
            pulse(in1, in2, False, True, pulse_ms, settle_ms)
            write_ircut_mode("night")
            print(f"IR-CUT switched to NIGHT using GPIO{in1_gpio}=0, GPIO{in2_gpio}=1 pulse")
            return 0
    return 1


def write_ircut_mode(mode: str) -> None:
    write_json(IRCUT_MODE_FILE, {"mode": mode, "updated_at": time.time()})
