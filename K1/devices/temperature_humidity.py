from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from k1edge.runtime_store import read_json, write_json
from utils.paths import TEMPERATURE_HUMIDITY_LOG, TEMPERATURE_HUMIDITY_STATUS_FILE


DEFAULT_GPIO = 92
DEFAULT_CHIP = "gpiochip0"
DEFAULT_INTERVAL_MS = 2500
DEFAULT_PULL_UP = True
DEFAULT_ATTEMPTS = 5
DEFAULT_RETRY_MS = 1200
DEFAULT_HELPER = PROJECT_ROOT / "devices" / "dht11_reader"
SENSOR_NAME = "DHT11"

READ_OK = "OK"
ERR_SENSOR_NO_RESPONSE_LOW = "ERR_SENSOR_NO_RESPONSE_LOW"
ERR_SENSOR_NO_RESPONSE_HIGH = "ERR_SENSOR_NO_RESPONSE_HIGH"
ERR_BIT_START_TIMEOUT = "ERR_BIT_START_TIMEOUT"
ERR_BIT_VALUE_TIMEOUT = "ERR_BIT_VALUE_TIMEOUT"
ERR_CHECKSUM = "ERR_CHECKSUM"
ERR_RANGE = "ERR_RANGE"
ERR_HELPER_UNAVAILABLE = "ERR_HELPER_UNAVAILABLE"
ERR_GPIOD_UNAVAILABLE = "ERR_GPIOD_UNAVAILABLE"

STATUS_TEXT = {
    READ_OK: "OK",
    ERR_SENSOR_NO_RESPONSE_LOW: "No response: DATA did not go LOW. Check VCC/GND/DATA pin.",
    ERR_SENSOR_NO_RESPONSE_HIGH: "No response: DATA did not go HIGH. Check pull-up or sensor.",
    ERR_BIT_START_TIMEOUT: "Timeout while waiting for a data bit to start.",
    ERR_BIT_VALUE_TIMEOUT: "Timeout while measuring a data bit.",
    ERR_CHECKSUM: "Checksum error. Data line may be noisy or timing is unstable.",
    ERR_RANGE: "Reading passed checksum but is outside the physical range.",
    ERR_HELPER_UNAVAILABLE: "DHT11 C helper is not available or did not return JSON.",
    ERR_GPIOD_UNAVAILABLE: "python gpiod is not available on this system.",
}


class TemperatureHumidityError(RuntimeError):
    pass


class Dht11ReadError(TemperatureHumidityError):
    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(STATUS_TEXT.get(status, status))


@dataclass(frozen=True)
class Dht11Reading:
    humidity_percent: float
    temperature_c: float
    raw: list[int]


class TemperatureHumidityController:
    def __init__(
        self,
        gpio: int = DEFAULT_GPIO,
        chip: str = DEFAULT_CHIP,
        interval_ms: int = DEFAULT_INTERVAL_MS,
        pull_up: bool = DEFAULT_PULL_UP,
        status_path: Path = TEMPERATURE_HUMIDITY_STATUS_FILE,
    ) -> None:
        self.gpio = int(gpio)
        self.chip = str(chip)
        self.interval_ms = int(interval_ms)
        self.pull_up = bool(pull_up)
        self.status_path = status_path
        self._lock = threading.Lock()

    def close(self) -> None:
        return

    def status(self) -> dict[str, Any]:
        with self._lock:
            status = read_json_file(self.status_path)
            if status.get("gpio") != self.gpio or status.get("chip") not in (None, self.chip):
                status = {}
            updated_at = status.get("updated_at")
            age_s = round(time.time() - float(updated_at), 1) if updated_at else None
            last_success_at = status.get("last_success_at")
            last_success_age_s = round(time.time() - float(last_success_at), 1) if last_success_at else None
            last_error_at = status.get("last_error_at")
            last_error_age_s = round(time.time() - float(last_error_at), 1) if last_error_at else None
            return {
                "sensor": SENSOR_NAME,
                "gpio": self.gpio,
                "chip": self.chip,
                "interval_ms": self.interval_ms,
                "pull_up": self.pull_up,
                "connected": bool(status.get("running")),
                "available": self.status_path.exists(),
                "temperature_c": status.get("temperature_c"),
                "humidity_percent": status.get("humidity_percent"),
                "has_reading": bool(status.get("has_reading")),
                "status": status.get("status"),
                "message": status.get("message"),
                "ok_count": status.get("ok_count", 0),
                "fail_count": status.get("fail_count", 0),
                "updated_at": updated_at,
                "age_s": age_s,
                "last_success_at": last_success_at,
                "last_success_age_s": last_success_age_s,
                "last_error_at": last_error_at,
                "last_error_age_s": last_error_age_s,
                "error": status.get("error"),
            }


class TemperatureHumidityWorker:
    def __init__(
        self,
        gpio: int = DEFAULT_GPIO,
        chip: str = DEFAULT_CHIP,
        interval_ms: int = DEFAULT_INTERVAL_MS,
        pull_up: bool = DEFAULT_PULL_UP,
        attempts: int = DEFAULT_ATTEMPTS,
        retry_ms: int = DEFAULT_RETRY_MS,
        helper: Path | str | None = DEFAULT_HELPER,
        status_path: Path = TEMPERATURE_HUMIDITY_STATUS_FILE,
        log_path: Path = TEMPERATURE_HUMIDITY_LOG,
    ) -> None:
        self.gpio = int(gpio)
        self.chip = str(chip)
        self.interval_s = max(int(interval_ms), 2000) / 1000.0
        self.interval_ms = int(round(self.interval_s * 1000))
        self.pull_up = bool(pull_up)
        self.attempts = max(1, int(attempts))
        self.retry_s = max(int(retry_ms), 1100) / 1000.0
        self.retry_ms = int(round(self.retry_s * 1000))
        self.helper = Path(helper) if helper else None
        self.status_path = status_path
        self.log_path = log_path
        self._running = True
        self._ok_count = 0
        self._fail_count = 0
        self._last_temperature_c: float | None = None
        self._last_humidity_percent: float | None = None
        self._last_raw: list[int] | None = None
        self._last_status = READ_OK
        self._last_error: str | None = None
        self._last_success_at: float | None = None
        self._last_error_at: float | None = None

    def run(self, once: bool = False) -> int:
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        self._log("starting temperature humidity worker")
        self._write_status(READ_OK, None, running=True)
        try:
            while self._running:
                ok = self._sample_once()
                if once:
                    return 0 if ok else 1
                self._sleep_interval()
            return 0
        finally:
            self._write_status(self._last_status, self._last_error, running=False)
            self._log("stopped temperature humidity worker")

    def _stop(self, signum: int, frame: Any) -> None:
        self._running = False

    def _sample_once(self) -> bool:
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                reading = read_dht11(
                    self.gpio,
                    chip_name=self.chip,
                    pull_up=self.pull_up,
                    helper_path=self.helper,
                )
                return self._record_reading(reading, attempt)
            except Dht11ReadError as exc:
                last_error = exc
                if attempt >= self.attempts:
                    break
                self._log(f"GPIO{self.gpio} attempt {attempt}/{self.attempts} {exc.status}: {exc}")
                self._sleep_retry()
            except Exception as exc:
                last_error = exc
                break

        if isinstance(last_error, Dht11ReadError):
            self._fail_count += 1
            self._last_error_at = time.time()
            self._write_status(last_error.status, str(last_error), running=True)
            self._log(f"GPIO{self.gpio} {last_error.status}: {last_error}")
            return False
        if last_error is not None:
            self._fail_count += 1
            self._last_error_at = time.time()
            self._write_status(type(last_error).__name__, str(last_error), running=True)
            self._log(f"GPIO{self.gpio} error: {last_error}")
            return False
        return False

    def _record_reading(self, reading: Dht11Reading, attempt: int) -> bool:
        self._ok_count += 1
        self._last_temperature_c = reading.temperature_c
        self._last_humidity_percent = reading.humidity_percent
        self._last_raw = reading.raw
        self._last_success_at = time.time()
        self._write_status(READ_OK, None, running=True)
        retry_text = "" if attempt == 1 else f" after {attempt} attempts"
        self._log(
            f"GPIO{self.gpio} humidity={reading.humidity_percent:.1f}% "
            f"temperature={reading.temperature_c:.1f}C{retry_text}"
        )
        return True

    def _sleep_retry(self) -> None:
        deadline = time.monotonic() + self.retry_s
        while self._running and time.monotonic() < deadline:
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    def _sleep_interval(self) -> None:
        deadline = time.monotonic() + self.interval_s
        while self._running and time.monotonic() < deadline:
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    def _write_status(self, status: str, error: str | None, running: bool) -> None:
        self._last_status = status
        self._last_error = error
        message = STATUS_TEXT.get(status, status)
        now = time.time()
        write_json_file(
            self.status_path,
            {
                "running": running,
                "sensor": SENSOR_NAME,
                "gpio": self.gpio,
                "chip": self.chip,
                "interval_ms": self.interval_ms,
                "pull_up": self.pull_up,
                "attempts": self.attempts,
                "retry_ms": self.retry_ms,
                "helper": str(self.helper) if self.helper else None,
                "temperature_c": self._last_temperature_c,
                "humidity_percent": self._last_humidity_percent,
                "raw": self._last_raw,
                "has_reading": self._last_temperature_c is not None and self._last_humidity_percent is not None,
                "status": status,
                "message": message,
                "ok_count": self._ok_count,
                "fail_count": self._fail_count,
                "updated_at": now,
                "last_success_at": self._last_success_at,
                "last_error_at": self._last_error_at,
                "error": error,
            },
        )

    def _log(self, message: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} {message}\n")


def read_dht11(
    gpio: int,
    chip_name: str = DEFAULT_CHIP,
    pull_up: bool = DEFAULT_PULL_UP,
    helper_path: Path | str | None = DEFAULT_HELPER,
) -> Dht11Reading:
    if helper_path is not None:
        helper_path = Path(helper_path)
    if helper_path and helper_path.exists():
        return read_dht11_with_helper(helper_path, gpio, chip_name=chip_name, pull_up=pull_up)
    return read_dht11_python(gpio, chip_name=chip_name, pull_up=pull_up)


def read_dht11_with_helper(
    helper_path: Path,
    gpio: int,
    chip_name: str = DEFAULT_CHIP,
    pull_up: bool = DEFAULT_PULL_UP,
) -> Dht11Reading:
    command = [
        str(helper_path),
        "--gpio",
        str(int(gpio)),
        "--chip",
        chip_name,
    ]
    command.append("--pull-up" if pull_up else "--no-pull-up")
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=1.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Dht11ReadError(ERR_HELPER_UNAVAILABLE) from exc

    raw_output = (completed.stdout or "").strip().splitlines()
    if not raw_output:
        raise Dht11ReadError(ERR_HELPER_UNAVAILABLE)
    try:
        payload = json.loads(raw_output[-1])
    except json.JSONDecodeError as exc:
        raise Dht11ReadError(ERR_HELPER_UNAVAILABLE) from exc

    status = str(payload.get("status") or ERR_HELPER_UNAVAILABLE)
    if completed.returncode != 0 or not payload.get("ok"):
        raise Dht11ReadError(status)

    reading = Dht11Reading(
        humidity_percent=round(float(payload["humidity_percent"]), 1),
        temperature_c=round(float(payload["temperature_c"]), 1),
        raw=[int(value) for value in payload.get("raw", [])[:5]],
    )
    validate_reading(reading)
    return reading


def validate_reading(reading: Dht11Reading) -> None:
    if not (20.0 <= reading.humidity_percent <= 90.0):
        raise Dht11ReadError(ERR_RANGE)
    if not (0.0 <= reading.temperature_c <= 50.0):
        raise Dht11ReadError(ERR_RANGE)


def read_dht11_python(gpio: int, chip_name: str = DEFAULT_CHIP, pull_up: bool = DEFAULT_PULL_UP) -> Dht11Reading:
    try:
        import gpiod
    except ImportError as exc:
        raise Dht11ReadError(ERR_GPIOD_UNAVAILABLE) from exc

    chip = gpiod.Chip(chip_name)
    line = chip.get_line(int(gpio))
    try:
        request_open_drain_output(line, gpiod, default_value=1, pull_up=pull_up)
        line.set_value(1)
        time.sleep(0.001)
        line.set_value(0)
        time.sleep(0.020)
        line.set_value(1)
        delay_us(30)

        if not wait_for_level(line, 0, 200):
            raise Dht11ReadError(ERR_SENSOR_NO_RESPONSE_LOW)
        if not wait_for_level(line, 1, 200):
            raise Dht11ReadError(ERR_SENSOR_NO_RESPONSE_HIGH)
        if not wait_for_level(line, 0, 200):
            raise Dht11ReadError(ERR_SENSOR_NO_RESPONSE_LOW)

        data = [0, 0, 0, 0, 0]
        for bit_index in range(40):
            if not wait_for_level(line, 1, 200):
                raise Dht11ReadError(ERR_BIT_START_TIMEOUT)

            high_start = monotonic_us()
            if not wait_for_level(line, 0, 200):
                raise Dht11ReadError(ERR_BIT_VALUE_TIMEOUT)
            high_time = monotonic_us() - high_start

            byte_index = bit_index // 8
            data[byte_index] = (data[byte_index] << 1) & 0xFF
            if high_time > 50:
                data[byte_index] |= 1

        checksum = sum(data[:4]) & 0xFF
        if checksum != data[4]:
            raise Dht11ReadError(ERR_CHECKSUM)

        return decode_dht11_bytes(data)
    finally:
        try:
            line.release()
        except OSError:
            pass
        close = getattr(chip, "close", None)
        if callable(close):
            close()


def request_open_drain_output(line: Any, gpiod: Any, default_value: int, pull_up: bool) -> None:
    flags = getattr(gpiod, "LINE_REQ_FLAG_OPEN_DRAIN", 0)
    if pull_up:
        flags |= getattr(gpiod, "LINE_REQ_FLAG_BIAS_PULL_UP", 0)
    try:
        line.request(
            consumer="temperature-humidity",
            type=gpiod.LINE_REQ_DIR_OUT,
            flags=flags,
            default_vals=[int(default_value)],
        )
    except OSError:
        if not pull_up:
            raise
        flags = getattr(gpiod, "LINE_REQ_FLAG_OPEN_DRAIN", 0)
        line.request(
            consumer="temperature-humidity",
            type=gpiod.LINE_REQ_DIR_OUT,
            flags=flags,
            default_vals=[int(default_value)],
        )
    except TypeError:
        line.request("temperature-humidity", gpiod.LINE_REQ_DIR_OUT, flags, [int(default_value)])


def wait_for_level(line: Any, expected_level: int, timeout_us: int) -> bool:
    deadline = monotonic_us() + int(timeout_us)
    while monotonic_us() <= deadline:
        if int(line.get_value()) == int(expected_level):
            return True
    return False


def delay_us(duration_us: int) -> None:
    deadline = monotonic_us() + int(duration_us)
    while monotonic_us() < deadline:
        pass


def monotonic_us() -> int:
    return time.monotonic_ns() // 1000


def read_json_file(path: Path) -> dict[str, Any]:
    return read_json(path)


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    write_json(path, payload)


def decode_dht11_bytes(data: list[int]) -> Dht11Reading:
    if len(data) != 5 or any(value < 0 or value > 255 for value in data):
        raise Dht11ReadError(ERR_RANGE)
    if data[1] > 9 or data[3] > 9:
        raise Dht11ReadError(ERR_RANGE)
    humidity = data[0] + data[1] / 10.0
    temperature = (data[2] & 0x7F) + data[3] / 10.0
    if data[2] & 0x80:
        temperature = -temperature
    reading = Dht11Reading(
        humidity_percent=round(humidity, 1),
        temperature_c=round(temperature, 1),
        raw=data.copy(),
    )
    validate_reading(reading)
    return reading


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="K1 DHT11 temperature humidity worker")
    parser.add_argument("--gpio", type=int, default=DEFAULT_GPIO)
    parser.add_argument("--chip", default=DEFAULT_CHIP)
    parser.add_argument("--interval-ms", type=int, default=DEFAULT_INTERVAL_MS)
    parser.add_argument("--pull-up", dest="pull_up", action="store_true", default=DEFAULT_PULL_UP)
    parser.add_argument("--no-pull-up", dest="pull_up", action="store_false")
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    parser.add_argument("--retry-ms", type=int, default=DEFAULT_RETRY_MS)
    parser.add_argument("--helper", type=Path, default=DEFAULT_HELPER)
    parser.add_argument("--no-helper", dest="helper", action="store_const", const=None)
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return TemperatureHumidityWorker(
        gpio=args.gpio,
        chip=args.chip,
        interval_ms=args.interval_ms,
        pull_up=args.pull_up,
        attempts=args.attempts,
        retry_ms=args.retry_ms,
        helper=args.helper,
    ).run(once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
