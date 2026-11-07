"""Mirrored PWM4 + PWM7 output (fill light), controlled from the 7860 main service.

Both channels always receive the same frequency, duty cycle and enable state.

    7860 /api/light -> DualPwmClient -> runtime/state/pwm_dual_command.json
      -> pwm-control.service (DualPwmWorker, root) -> DualPwmController -> PWM sysfs
      -> runtime/state/pwm_dual_status.json -> DualPwmClient.status() -> /api/status

Only the worker writes these PWM sysfs nodes; it must run as root (sysfs
export + /dev/mem pinmux). The main service never touches PWM hardware.

Header mapping (MUSE Pi Pro, from the board DTB pinctrl groups):

    PWM4  d401b000.pwm  GPIO34  header pin 32  pinmux 0xd401e08c = 0xd043 (func 3)
    PWM7  d401bc00.pwm  GPIO37  header pin 40  pinmux 0xd401e098 = 0xd042 (func 2)
"""

from __future__ import annotations

import argparse
import math
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from k1edge.runtime_store import read_json, write_json
from utils.paths import RUNTIME_DIR


PWM_SYSFS_ROOT = Path("/sys/class/pwm")
DUAL_PWM_STATE_FILE = RUNTIME_DIR / "state" / "pwm_dual.json"
DUAL_PWM_COMMAND_FILE = RUNTIME_DIR / "state" / "pwm_dual_command.json"
DUAL_PWM_STATUS_FILE = RUNTIME_DIR / "state" / "pwm_dual_status.json"
DUAL_PWM_LOG = RUNTIME_DIR / "logs" / "pwm_dual.log"
WORKER_STALE_AFTER_S = 5.0

DEFAULT_FREQUENCY_HZ = 10_000
MIN_FREQUENCY_HZ = 100
MAX_FREQUENCY_HZ = 500_000


class PwmControlError(RuntimeError):
    pass


@dataclass(frozen=True)
class PwmChannelSpec:
    name: str
    pwm_device: str
    channel: int
    gpio: int
    header_pin: int
    pinmux_address: int | None
    pinmux_value: int | None


DEFAULT_CHANNELS: tuple[PwmChannelSpec, ...] = (
    PwmChannelSpec("pwm4", "d401b000.pwm", 0, gpio=34, header_pin=32, pinmux_address=0xD401E08C, pinmux_value=0x0000D043),
    PwmChannelSpec("pwm7", "d401bc00.pwm", 0, gpio=37, header_pin=40, pinmux_address=0xD401E098, pinmux_value=0x0000D042),
)


def normalize_duty(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("duty must be a finite number")
    if number < 0.0 or number > 100.0:
        raise ValueError("duty must be between 0 and 100")
    return round(number, 1)


def normalize_frequency(value: Any) -> int:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("frequency_hz must be a finite number")
    frequency = int(round(number))
    if frequency < MIN_FREQUENCY_HZ or frequency > MAX_FREQUENCY_HZ:
        raise ValueError(f"frequency_hz must be between {MIN_FREQUENCY_HZ} and {MAX_FREQUENCY_HZ}")
    return frequency


def period_ns_for(frequency_hz: int) -> int:
    return int(round(1_000_000_000 / frequency_hz))


def duty_ns_for(duty: float, period_ns: int, active_low: bool) -> int:
    hardware_duty = 100.0 - duty if active_low else duty
    return int(round(period_ns * hardware_duty / 100.0))


def find_pwmchip(pwm_device: str, pwm_root: Path = PWM_SYSFS_ROOT) -> Path | None:
    if not pwm_root.exists():
        return None
    for chip in sorted(pwm_root.glob("pwmchip*")):
        try:
            resolved = str(chip.resolve())
        except OSError:
            continue
        if pwm_device in resolved:
            return chip
    return None


class SysfsPwmChannel:
    def __init__(self, spec: PwmChannelSpec, chip_path: Path | None = None, pwm_root: Path = PWM_SYSFS_ROOT) -> None:
        self.spec = spec
        self._chip_override = chip_path
        self._pwm_root = pwm_root
        self._chip: Path | None = chip_path

    @property
    def chip(self) -> Path:
        if self._chip is None:
            self._chip = find_pwmchip(self.spec.pwm_device, self._pwm_root)
        if self._chip is None:
            raise PwmControlError(f"{self.spec.name}: PWM chip for {self.spec.pwm_device} not found")
        return self._chip

    @property
    def path(self) -> Path:
        return self.chip / f"pwm{self.spec.channel}"

    def ensure_exported(self) -> None:
        if self.path.exists():
            return
        try:
            (self.chip / "export").write_text(str(self.spec.channel))
        except OSError as exc:
            if not self.path.exists():
                raise PwmControlError(f"{self.spec.name}: export failed: {exc}") from exc
        deadline = time.monotonic() + 1.0
        while not self.path.exists():
            if time.monotonic() > deadline:
                raise PwmControlError(f"{self.spec.name}: timed out waiting for {self.path}")
            time.sleep(0.02)

    def write(self, name: str, value: int | str, ignore_errors: bool = False) -> None:
        try:
            (self.path / name).write_text(str(value))
        except OSError as exc:
            if ignore_errors:
                return
            raise PwmControlError(f"{self.spec.name}: write {name}={value} failed: {exc}") from exc

    def read(self, name: str) -> str | None:
        try:
            return (self.path / name).read_text().strip()
        except (OSError, PwmControlError):
            return None

    def readback(self) -> dict[str, Any]:
        def as_int(value: str | None) -> int | None:
            try:
                return int(value) if value is not None else None
            except ValueError:
                return None

        return {
            "enable": as_int(self.read("enable")),
            "period_ns": as_int(self.read("period")),
            "duty_cycle_ns": as_int(self.read("duty_cycle")),
        }


def read_physical_u32(address: int) -> int:
    import mmap
    import struct

    page_size = mmap.PAGESIZE
    page_base = address & ~(page_size - 1)
    offset = address - page_base
    with open("/dev/mem", "r+b", buffering=0) as handle:
        with mmap.mmap(handle.fileno(), page_size, offset=page_base) as mem:
            return struct.unpack("<I", mem[offset : offset + 4])[0]


def write_physical_u32(address: int, value: int) -> None:
    import mmap
    import struct

    page_size = mmap.PAGESIZE
    page_base = address & ~(page_size - 1)
    offset = address - page_base
    with open("/dev/mem", "r+b", buffering=0) as handle:
        with mmap.mmap(handle.fileno(), page_size, offset=page_base) as mem:
            struct.pack_into("<I", mem, offset, value & 0xFFFFFFFF)


class DualPwmController:
    """Drives every configured channel with identical settings."""

    def __init__(
        self,
        channels: Iterable[PwmChannelSpec] = DEFAULT_CHANNELS,
        frequency_hz: int = DEFAULT_FREQUENCY_HZ,
        active_low: bool = False,
        apply_pinmux: bool = True,
        state_path: Path = DUAL_PWM_STATE_FILE,
        log_path: Path | None = DUAL_PWM_LOG,
        pwm_root: Path = PWM_SYSFS_ROOT,
        chip_paths: dict[str, Path] | None = None,
        pinmux_reader: Callable[[int], int] = read_physical_u32,
        pinmux_writer: Callable[[int, int], None] = write_physical_u32,
    ) -> None:
        chip_paths = chip_paths or {}
        self.channels = [SysfsPwmChannel(spec, chip_paths.get(spec.name), pwm_root) for spec in channels]
        if not self.channels:
            raise ValueError("at least one PWM channel is required")
        self.active_low = bool(active_low)
        self.apply_pinmux = bool(apply_pinmux)
        self.state_path = state_path
        self.log_path = log_path
        self._pinmux_reader = pinmux_reader
        self._pinmux_writer = pinmux_writer
        self._lock = threading.Lock()
        self._enabled = False
        self._duty = 0.0
        self._frequency_hz = normalize_frequency(frequency_hz)
        self._applied_period_ns: int | None = None
        self._output_on = False
        self._error: str | None = None
        self._updated_at: float | None = None

    # ---- lifecycle -----------------------------------------------------

    def start(self) -> dict[str, Any]:
        saved = read_json(self.state_path)
        enabled = bool(saved.get("enabled", False))
        try:
            duty = normalize_duty(saved.get("duty", 0.0))
        except (TypeError, ValueError):
            duty = 0.0
        try:
            frequency = normalize_frequency(saved.get("frequency_hz", self._frequency_hz))
        except (TypeError, ValueError):
            frequency = self._frequency_hz
        self._log(f"start: restore enabled={enabled} duty={duty} frequency_hz={frequency}")
        with self._lock:
            self._applied_period_ns = None
            self._duty, self._frequency_hz = duty, frequency
            try:
                self._apply_locked(enabled, duty, frequency, persist=False)
            except PwmControlError:
                pass  # error is recorded in status; keep serving so it can be seen and retried
            return self._status_locked()

    def stop(self) -> None:
        with self._lock:
            self._force_all_off_locked()
            self._applied_period_ns = None
            self._log("stop: outputs disabled")

    # ---- control -------------------------------------------------------

    def apply(
        self,
        enabled: bool | None = None,
        duty: Any = None,
        frequency_hz: Any = None,
    ) -> dict[str, Any]:
        new_duty = normalize_duty(duty) if duty is not None else None
        new_frequency = normalize_frequency(frequency_hz) if frequency_hz is not None else None
        with self._lock:
            self._apply_locked(
                self._enabled if enabled is None else bool(enabled),
                self._duty if new_duty is None else new_duty,
                self._frequency_hz if new_frequency is None else new_frequency,
                persist=True,
            )
            return self._status_locked()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_locked()

    # ---- internals -----------------------------------------------------

    def _apply_locked(self, enabled: bool, duty: float, frequency_hz: int, persist: bool) -> None:
        try:
            self._drive_locked(enabled, duty, frequency_hz)
        except Exception as exc:
            self._error = str(exc)
            self._log(f"error: {exc}; disabling all channels to keep outputs identical")
            self._force_all_off_locked()
            # Outputs are now off on every channel; force a full re-program on the next apply.
            self._enabled = False
            self._applied_period_ns = None
            self._updated_at = time.time()
            raise PwmControlError(str(exc)) from exc
        self._enabled, self._duty, self._frequency_hz = enabled, duty, frequency_hz
        self._error = None
        self._updated_at = time.time()
        if persist:
            write_json(
                self.state_path,
                {
                    "enabled": enabled,
                    "duty": duty,
                    "frequency_hz": frequency_hz,
                    "active_low": self.active_low,
                    "updated_at": self._updated_at,
                },
            )
        self._log(f"apply: enabled={enabled} duty={duty}% frequency_hz={frequency_hz}")

    def _drive_locked(self, enabled: bool, duty: float, frequency_hz: int) -> None:
        for channel in self.channels:
            channel.ensure_exported()

        period_ns = period_ns_for(frequency_hz)
        duty_ns = duty_ns_for(duty, period_ns, self.active_low)
        want_on = enabled and duty_ns > 0

        if self._applied_period_ns != period_ns:
            # Period change: park every channel first so duty_cycle <= period always holds.
            # A freshly exported channel has period=0, and the kernel rejects any write
            # (EINVAL) until a period is set; its duty is necessarily 0 already.
            for channel in self.channels:
                channel.write("enable", 0, ignore_errors=True)
                if channel.read("period") not in (None, "0"):
                    channel.write("duty_cycle", 0)
            for channel in self.channels:
                channel.write("period", period_ns)
            self._applied_period_ns = period_ns
            self._output_on = False

        if not want_on:
            for channel in self.channels:
                channel.write("duty_cycle", 0)
            for channel in self.channels:
                channel.write("enable", 0, ignore_errors=True)
            self._output_on = False
        else:
            # Duty-only changes do not touch period or enable, so a running output never blinks.
            for channel in self.channels:
                channel.write("duty_cycle", duty_ns)
            if not self._output_on:
                for channel in self.channels:
                    channel.write("enable", 1)
                self._output_on = True

        if self.apply_pinmux:
            self._ensure_pinmux_locked()

    def _ensure_pinmux_locked(self) -> None:
        for channel in self.channels:
            spec = channel.spec
            if spec.pinmux_address is None or spec.pinmux_value is None:
                continue
            try:
                current = self._pinmux_reader(spec.pinmux_address)
                if current != spec.pinmux_value:
                    self._pinmux_writer(spec.pinmux_address, spec.pinmux_value)
                    self._log(
                        f"{spec.name}: pinmux 0x{spec.pinmux_address:x} 0x{current:08x} -> 0x{spec.pinmux_value:08x}"
                    )
            except OSError as exc:
                raise PwmControlError(f"{spec.name}: pinmux 0x{spec.pinmux_address:x} failed: {exc}") from exc

    def _force_all_off_locked(self) -> None:
        for channel in self.channels:
            try:
                if channel.path.exists():
                    channel.write("duty_cycle", 0, ignore_errors=True)
                    channel.write("enable", 0, ignore_errors=True)
            except PwmControlError:
                pass
        self._output_on = False

    def _status_locked(self) -> dict[str, Any]:
        period_ns = period_ns_for(self._frequency_hz)
        expected = {
            "enable": 1 if self._output_on else 0,
            "period_ns": period_ns,
            "duty_cycle_ns": duty_ns_for(self._duty, period_ns, self.active_low) if self._output_on else 0,
        }
        channels = []
        for channel in self.channels:
            try:
                chip = str(channel.chip)
            except PwmControlError:
                chip = None
            readback = channel.readback() if chip else {"enable": None, "period_ns": None, "duty_cycle_ns": None}
            channels.append(
                {
                    "name": channel.spec.name,
                    "pwm_device": channel.spec.pwm_device,
                    "gpio": channel.spec.gpio,
                    "header_pin": channel.spec.header_pin,
                    "pwmchip": chip,
                    "readback": readback,
                    "matches_target": readback == expected,
                }
            )
        readbacks = [item["readback"] for item in channels]
        return {
            "enabled": self._enabled,
            "duty": self._duty,
            "frequency_hz": self._frequency_hz,
            "period_ns": period_ns,
            "active_low": self.active_low,
            "output_on": self._output_on,
            "outputs_identical": all(item == readbacks[0] for item in readbacks),
            "channels": channels,
            "error": self._error,
            "updated_at": self._updated_at,
            "limits": {"min_frequency_hz": MIN_FREQUENCY_HZ, "max_frequency_hz": MAX_FREQUENCY_HZ},
        }

    def _log(self, message: str) -> None:
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
        except OSError:
            pass


class DualPwmWorker:
    """Root worker: applies the latest command file to both channels and publishes status."""

    def __init__(
        self,
        controller: DualPwmController,
        command_path: Path = DUAL_PWM_COMMAND_FILE,
        status_path: Path = DUAL_PWM_STATUS_FILE,
        poll_ms: int = 50,
        status_interval_s: float = 1.0,
    ) -> None:
        self.controller = controller
        self.command_path = command_path
        self.status_path = status_path
        self.poll_s = max(int(poll_ms), 10) / 1000.0
        self.status_interval_s = float(status_interval_s)
        self._running = True
        self._last_command_mtime: float | None = None
        self._last_status_write = 0.0
        self._command_error: str | None = None

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        self.controller.start()
        try:
            self.poll_once(force_command=True)
            while self._running:
                time.sleep(self.poll_s)
                self.poll_once()
            return 0
        finally:
            self.controller.stop()
            self._write_status(running=False)

    def _stop(self, signum: int, frame: Any) -> None:
        self._running = False

    def poll_once(self, force_command: bool = False) -> None:
        try:
            mtime: float | None = self.command_path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime is not None and (force_command or mtime != self._last_command_mtime):
            self._last_command_mtime = mtime
            self._apply_command(read_json(self.command_path))
            self._write_status()
        elif time.monotonic() - self._last_status_write >= self.status_interval_s:
            self._write_status()

    def _apply_command(self, command: dict[str, Any]) -> None:
        if not command:
            return
        enabled = command.get("enabled")
        try:
            self.controller.apply(
                enabled=enabled if isinstance(enabled, bool) else None,
                duty=command.get("duty"),
                frequency_hz=command.get("frequency_hz"),
            )
            self._command_error = None
        except (TypeError, ValueError) as exc:
            self._command_error = f"invalid command: {exc}"
        except PwmControlError:
            self._command_error = None  # hardware error is reported by controller.status()

    def _write_status(self, running: bool = True) -> None:
        self._last_status_write = time.monotonic()
        status = self.controller.status()
        if self._command_error and not status["error"]:
            status["error"] = self._command_error
        write_json(self.status_path, {**status, "running": running, "status_at": time.time()})


class DualPwmClient:
    """Used by the 7860 main service: writes commands, reads worker status. Never touches hardware."""

    def __init__(
        self,
        command_path: Path = DUAL_PWM_COMMAND_FILE,
        status_path: Path = DUAL_PWM_STATUS_FILE,
        ack_timeout_s: float = 0.5,
    ) -> None:
        self.command_path = command_path
        self.status_path = status_path
        self.ack_timeout_s = float(ack_timeout_s)
        self._lock = threading.Lock()

    def close(self) -> None:
        return

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_locked()

    def set_duty(self, duty: Any, frequency_hz: Any = None, enabled: bool | None = None) -> dict[str, Any]:
        number = float(duty)
        if not math.isfinite(number):
            raise ValueError("duty must be a finite number")
        duty_value = round(max(0.0, min(100.0, number)), 1)
        command: dict[str, Any] = {
            "duty": duty_value,
            "enabled": duty_value > 0 if enabled is None else bool(enabled),
        }
        if frequency_hz not in (None, ""):
            command["frequency_hz"] = normalize_frequency(frequency_hz)
        with self._lock:
            sent_at = time.time()
            command["updated_at"] = sent_at
            write_json(self.command_path, command)
            deadline = time.monotonic() + self.ack_timeout_s
            while time.monotonic() < deadline:
                if float(read_json(self.status_path).get("status_at") or 0) >= sent_at:
                    break
                time.sleep(0.02)
            status = self._status_locked()
            status["target_duty"] = duty_value
            return status

    def _status_locked(self) -> dict[str, Any]:
        status = read_json(self.status_path)
        command = read_json(self.command_path)
        status_at = status.get("status_at")
        try:
            age_s = round(time.time() - float(status_at), 1) if status_at else None
        except (TypeError, ValueError):
            age_s = None
        connected = bool(status.get("running")) and age_s is not None and age_s <= WORKER_STALE_AFTER_S
        return {
            "mode": "dual_pwm",
            "connected": connected,
            "available": bool(status),
            "enabled": status.get("enabled"),
            "duty": status.get("duty", command.get("duty")),
            "target_duty": command.get("duty"),
            "frequency_hz": status.get("frequency_hz", command.get("frequency_hz")),
            "output_on": status.get("output_on"),
            "outputs_identical": status.get("outputs_identical"),
            "channels": status.get("channels", []),
            "error": status.get("error"),
            "age_s": age_s,
            "updated_at": status.get("updated_at"),
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DeepZZZ K1 mirrored PWM4 + PWM7 worker (controlled from port 7860)")
    parser.add_argument("--frequency-hz", type=int, default=DEFAULT_FREQUENCY_HZ, help="initial frequency if no saved state")
    parser.add_argument("--active-low", action="store_true", help="invert duty (0%% = always high)")
    parser.add_argument("--no-pinmux", dest="apply_pinmux", action="store_false", help="do not touch pinmux registers")
    parser.add_argument("--poll-ms", type=int, default=50)
    # Accepted and ignored so an old unit file (which still passes --port 7863) does not crash-loop.
    parser.add_argument("--port", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    controller = DualPwmController(
        frequency_hz=args.frequency_hz,
        active_low=args.active_low,
        apply_pinmux=args.apply_pinmux,
    )
    return DualPwmWorker(controller, poll_ms=args.poll_ms).run()
