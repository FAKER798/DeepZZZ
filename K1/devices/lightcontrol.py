from __future__ import annotations

import argparse
import math
import mmap
import signal
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from k1edge.runtime_store import read_json, write_json
from utils.paths import LIGHT_COMMAND_FILE, LIGHT_LOG, LIGHT_STATUS_FILE


DEFAULT_GPIO = 74
DEFAULT_PWMCHIP = "auto"
DEFAULT_PWM_DEVICE = "d401b000.pwm"
DEFAULT_CHANNEL = 0
DEFAULT_FREQUENCY_HZ = 10_000
DEFAULT_POLL_MS = 50
DEFAULT_PINMUX_ADDRESS = 0xD401E08C
DEFAULT_PINMUX_VALUE = 0x0000D043
DEFAULT_ACTIVE_LOW = False


class LightControlError(RuntimeError):
    pass


class LightController:
    def __init__(
        self,
        gpio: int = DEFAULT_GPIO,
        frequency_hz: int = DEFAULT_FREQUENCY_HZ,
        pwmchip: str = DEFAULT_PWMCHIP,
        pwm_device: str = DEFAULT_PWM_DEVICE,
        channel: int = DEFAULT_CHANNEL,
        active_low: bool = DEFAULT_ACTIVE_LOW,
        command_path: Path = LIGHT_COMMAND_FILE,
        status_path: Path = LIGHT_STATUS_FILE,
    ) -> None:
        self.gpio = int(gpio)
        self.frequency_hz = int(frequency_hz)
        self.pwmchip = str(pwmchip)
        self.pwm_device = str(pwm_device)
        self.channel = int(channel)
        self.active_low = bool(active_low)
        self.command_path = command_path
        self.status_path = status_path
        self._lock = threading.Lock()

    def close(self) -> None:
        return

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_locked()

    def set_duty(self, duty: float) -> dict[str, Any]:
        duty_value = normalize_duty(duty)
        with self._lock:
            self._write_command(
                {
                    "duty": duty_value,
                    "gpio": self.gpio,
                    "frequency_hz": self.frequency_hz,
                    "pwmchip": self.pwmchip,
                    "pwm_device": self.pwm_device,
                    "channel": self.channel,
                    "active_low": self.active_low,
                    "updated_at": time.time(),
                }
            )
            time.sleep(0.08)
            status = self._status_locked()
            status["target_duty"] = duty_value
            if status["duty"] is None:
                status["duty"] = duty_value
            return status

    def _status_locked(self) -> dict[str, Any]:
        status = self._read_status()
        command = self._read_command()
        duty = status.get("duty", command.get("duty"))
        target_duty = command.get("duty")
        resolved_pwmchip = status.get("pwmchip") or resolve_pwmchip_path(self.pwmchip, self.pwm_device)
        return {
            "gpio": self.gpio,
            "frequency_hz": self.frequency_hz,
            "pwmchip": str(resolved_pwmchip or self.pwmchip),
            "pwm_device": self.pwm_device,
            "channel": self.channel,
            "active_low": self.active_low,
            "connected": bool(status.get("running")),
            "available": resolved_pwmchip is not None,
            "duty": duty,
            "target_duty": target_duty,
            "hardware_duty": status.get("hardware_duty"),
            "updated_at": status.get("updated_at") or command.get("updated_at"),
            "error": status.get("error"),
        }

    def _read_command(self) -> dict[str, Any]:
        return read_json_file(self.command_path)

    def _read_status(self) -> dict[str, Any]:
        status = read_json_file(self.status_path)
        if status.get("gpio") != self.gpio:
            return {}
        if status.get("frequency_hz") != self.frequency_hz:
            return {}
        if status.get("pwm_device") not in (None, self.pwm_device):
            return {}
        if self.pwmchip != "auto" and status.get("pwmchip") != self.pwmchip:
            return {}
        if status.get("channel") != self.channel:
            return {}
        if status.get("active_low") not in (None, self.active_low):
            return {}
        return status

    def _write_command(self, payload: dict[str, Any]) -> None:
        write_json_file(self.command_path, payload)


class HardwarePwmWorker:
    def __init__(
        self,
        gpio: int = DEFAULT_GPIO,
        frequency_hz: int = DEFAULT_FREQUENCY_HZ,
        pwmchip: str = DEFAULT_PWMCHIP,
        pwm_device: str = DEFAULT_PWM_DEVICE,
        channel: int = DEFAULT_CHANNEL,
        poll_ms: int = DEFAULT_POLL_MS,
        pinmux_address: int | None = DEFAULT_PINMUX_ADDRESS,
        pinmux_value: int | None = DEFAULT_PINMUX_VALUE,
        active_low: bool = DEFAULT_ACTIVE_LOW,
        command_path: Path = LIGHT_COMMAND_FILE,
        status_path: Path = LIGHT_STATUS_FILE,
        log_path: Path = LIGHT_LOG,
    ) -> None:
        self.gpio = int(gpio)
        self.frequency_hz = int(frequency_hz)
        self.pwmchip_request = str(pwmchip)
        self.pwm_device = str(pwm_device)
        self._pwmchip_path: Path | None = None
        self.channel = int(channel)
        self.poll_s = max(int(poll_ms), 10) / 1000.0
        self.pinmux_address = pinmux_address
        self.pinmux_value = pinmux_value
        self.active_low = bool(active_low)
        self.command_path = command_path
        self.status_path = status_path
        self.log_path = log_path
        self._running = True
        self._current_duty: float | None = None
        self._enabled = False
        self._last_command_mtime: float | None = None
        self._last_status_write = 0.0
        self._status_interval_s = 1.0

    @property
    def pwmchip(self) -> Path:
        if self._pwmchip_path is None:
            pwmchip_path = resolve_pwmchip_path(self.pwmchip_request, self.pwm_device)
            if pwmchip_path is None:
                detail = self.pwm_device if self.pwmchip_request == "auto" else self.pwmchip_request
                raise LightControlError(f"PWM chip not found: {detail}")
            self._pwmchip_path = pwmchip_path
        return self._pwmchip_path

    @property
    def pwm_path(self) -> Path:
        return self.pwmchip / f"pwm{self.channel}"

    @property
    def period_ns(self) -> int:
        return int(round(1_000_000_000 / self.frequency_hz))

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        self._log("starting hardware PWM worker")
        try:
            self._configure_pinmux()
            self._ensure_pwm()
            initial = read_json_file(self.command_path).get("duty", 0.0)
            self._apply_duty(normalize_duty(initial))
            while self._running:
                self._apply_command_if_changed()
                self._write_status_if_due()
                time.sleep(self.poll_s)
            return 0
        except Exception as exc:
            self._write_status(str(exc))
            self._log(f"error: {exc}")
            return 1
        finally:
            self._disable_pwm()
            self._write_status(None, running=False)
            self._log("stopped hardware PWM worker")

    def _stop(self, signum: int, frame: Any) -> None:
        self._running = False

    def _ensure_pwm(self) -> None:
        self._pwmchip_path = None
        _ = self.pwmchip
        if not self.pwm_path.exists():
            try:
                (self.pwmchip / "export").write_text(str(self.channel))
            except OSError as exc:
                if not self.pwm_path.exists():
                    raise LightControlError(f"failed to export PWM channel {self.channel}: {exc}") from exc
            wait_until_exists(self.pwm_path, timeout_s=1.0)
        self._write_file("enable", "0", ignore_errors=True)
        self._write_file("period", str(self.period_ns))
        self._write_file("duty_cycle", "0")
        self._enabled = False

    def _configure_pinmux(self) -> None:
        if self.pinmux_address is None or self.pinmux_value is None:
            return
        write_physical_u32(self.pinmux_address, self.pinmux_value)
        readback = read_physical_u32(self.pinmux_address)
        self._log(
            f"pinmux 0x{self.pinmux_address:x}=0x{self.pinmux_value:08x} readback=0x{readback:08x}"
        )

    def _apply_command_if_changed(self) -> None:
        try:
            mtime = self.command_path.stat().st_mtime
        except FileNotFoundError:
            return
        if self._last_command_mtime == mtime:
            return
        command = read_json_file(self.command_path)
        if command.get("gpio") not in (None, self.gpio):
            return
        if command.get("frequency_hz") not in (None, self.frequency_hz):
            return
        if command.get("pwm_device") not in (None, self.pwm_device):
            return
        command_pwmchip = command.get("pwmchip")
        if command_pwmchip not in (None, "auto", self.pwmchip_request, str(self._pwmchip_path or "")):
            return
        if command.get("channel") not in (None, self.channel):
            return
        if command.get("active_low") not in (None, self.active_low):
            return
        duty = normalize_duty(command.get("duty", 0.0))
        self._apply_duty(duty)
        self._last_command_mtime = mtime

    def _apply_duty(self, duty: float) -> None:
        if duty == self._current_duty:
            self._write_status(None)
            return
        hardware_duty = logical_to_hardware_duty(duty, self.active_low)
        duty_ns = duty_to_ns(hardware_duty, self.period_ns)
        if duty_ns <= 0:
            self._write_file("duty_cycle", "0")
            self._write_file("enable", "0", ignore_errors=True)
            self._enabled = False
        else:
            if not self._enabled:
                self._write_file("enable", "0", ignore_errors=True)
            self._write_file("period", str(self.period_ns))
            self._write_file("duty_cycle", str(duty_ns))
            if not self._enabled:
                self._write_file("enable", "1")
                self._enabled = True
        self._configure_pinmux()
        self._current_duty = duty
        self._write_status(None)
        self._log(
            f"GPIO{self.gpio} {self.pwmchip}/pwm{self.channel} "
            f"duty={duty}% hardware_duty={hardware_duty}% period={self.period_ns}ns"
        )

    def _disable_pwm(self) -> None:
        if self.pwm_path.exists():
            self._write_file("enable", "0", ignore_errors=True)
            self._write_file("duty_cycle", "0", ignore_errors=True)

    def _write_file(self, name: str, value: str, ignore_errors: bool = False) -> None:
        try:
            (self.pwm_path / name).write_text(value)
        except OSError as exc:
            if ignore_errors:
                return
            raise LightControlError(f"failed to write {self.pwm_path / name}={value}: {exc}") from exc

    def _write_status(self, error: str | None, running: bool | None = None) -> None:
        self._last_status_write = time.monotonic()
        write_json_file(
            self.status_path,
            {
                "running": self._running if running is None else running,
                "gpio": self.gpio,
                "frequency_hz": self.frequency_hz,
                "pwmchip": str(self._pwmchip_path or self.pwmchip_request),
                "pwm_device": self.pwm_device,
                "channel": self.channel,
                "active_low": self.active_low,
                "duty": self._current_duty,
                "hardware_duty": logical_to_hardware_duty(self._current_duty, self.active_low)
                if self._current_duty is not None
                else None,
                "enabled": self._enabled,
                "updated_at": time.time(),
                "error": error,
            },
        )

    def _write_status_if_due(self) -> None:
        if time.monotonic() - self._last_status_write >= self._status_interval_s:
            self._write_status(None)

    def _log(self, message: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} {message}\n")


def read_json_file(path: Path) -> dict[str, Any]:
    return read_json(path)


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    write_json(path, payload)


def normalize_duty(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("duty must be a finite number")
    return round(max(0.0, min(100.0, number)), 1)


def duty_to_ns(duty: float, period_ns: int) -> int:
    return int(round(period_ns * normalize_duty(duty) / 100.0))


def logical_to_hardware_duty(duty: float, active_low: bool) -> float:
    logical_duty = normalize_duty(duty)
    return round(100.0 - logical_duty, 1) if active_low else logical_duty


def wait_until_exists(path: Path, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise LightControlError(f"timed out waiting for {path}")


def write_physical_u32(address: int, value: int) -> None:
    page_size = mmap.PAGESIZE
    page_base = address & ~(page_size - 1)
    page_offset = address - page_base
    try:
        with open("/dev/mem", "r+b", buffering=0) as handle:
            with mmap.mmap(handle.fileno(), page_size, offset=page_base) as mem:
                struct.pack_into("<I", mem, page_offset, value & 0xFFFFFFFF)
    except OSError as exc:
        raise LightControlError(
            f"failed to write pinmux register 0x{address:x}=0x{value:08x}: {exc}"
        ) from exc


def read_physical_u32(address: int) -> int:
    page_size = mmap.PAGESIZE
    page_base = address & ~(page_size - 1)
    page_offset = address - page_base
    try:
        with open("/dev/mem", "r+b", buffering=0) as handle:
            with mmap.mmap(handle.fileno(), page_size, offset=page_base) as mem:
                return struct.unpack("<I", mem[page_offset : page_offset + 4])[0]
    except OSError as exc:
        raise LightControlError(f"failed to read pinmux register 0x{address:x}: {exc}") from exc


def resolve_pwmchip_path(pwmchip: str, pwm_device: str) -> Path | None:
    if pwmchip and pwmchip != "auto":
        path = Path(pwmchip)
        return path if path.exists() else None
    pwm_root = Path("/sys/class/pwm")
    if not pwm_root.exists():
        return None
    for chip in sorted(pwm_root.glob("pwmchip*")):
        try:
            resolved = chip.resolve()
        except OSError:
            continue
        if pwm_device and pwm_device in str(resolved):
            return chip
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="K1 hardware PWM light worker")
    parser.add_argument("--gpio", type=int, default=DEFAULT_GPIO)
    parser.add_argument("--frequency-hz", type=int, default=DEFAULT_FREQUENCY_HZ)
    parser.add_argument("--pwmchip", default=DEFAULT_PWMCHIP)
    parser.add_argument("--pwm-device", default=DEFAULT_PWM_DEVICE)
    parser.add_argument("--channel", type=int, default=DEFAULT_CHANNEL)
    parser.add_argument("--poll-ms", type=int, default=DEFAULT_POLL_MS)
    parser.add_argument("--pinmux-address", type=parse_optional_int, default=DEFAULT_PINMUX_ADDRESS)
    parser.add_argument("--pinmux-value", type=parse_optional_int, default=DEFAULT_PINMUX_VALUE)
    parser.add_argument("--active-low", dest="active_low", action="store_true", default=DEFAULT_ACTIVE_LOW)
    parser.add_argument("--active-high", dest="active_low", action="store_false")
    return parser


def parse_optional_int(value: str) -> int | None:
    if str(value).strip().lower() in {"", "none", "off", "disable", "disabled"}:
        return None
    return int(str(value), 0)


def main() -> int:
    args = build_parser().parse_args()
    return HardwarePwmWorker(
        gpio=args.gpio,
        frequency_hz=args.frequency_hz,
        pwmchip=args.pwmchip,
        pwm_device=args.pwm_device,
        channel=args.channel,
        poll_ms=args.poll_ms,
        pinmux_address=args.pinmux_address,
        pinmux_value=args.pinmux_value,
        active_low=args.active_low,
    ).run()


if __name__ == "__main__":
    raise SystemExit(main())
