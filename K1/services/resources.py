from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any


class ResourceMonitor:
    def __init__(self) -> None:
        self._available = hasattr(os, "sysconf") and Path("/proc/stat").exists()
        self._cpu_count = os.cpu_count() or 1
        self._last_total: tuple[int, int] | None = None
        self._last_proc: dict[int, tuple[int, float, str]] = {}

    def snapshot(self) -> dict[str, Any]:
        if not self._available:
            return unavailable_snapshot("linux /proc filesystem is not available")
        try:
            total = read_cpu_total()
        except OSError as exc:
            return unavailable_snapshot(str(exc))

        cpu_percent = None
        if self._last_total is not None:
            cpu_percent = cpu_usage_percent(self._last_total, total)
        self._last_total = total

        return {
            "updated_at": time.time(),
            "cpu_percent": round(cpu_percent, 1) if cpu_percent is not None else None,
            "cpu_count": self._cpu_count,
            "loadavg": read_loadavg(),
            "memory": read_memory(),
            "temperature_c": read_temperature(),
            "processes": self._processes(total[0]),
        }

    def _processes(self, total_jiffies: int) -> list[dict[str, Any]]:
        rows = []
        seen: set[int] = set()
        for pid, label, command in find_target_processes():
            seen.add(pid)
            ticks, rss_kb = read_process_stat(pid)
            cpu_percent = None
            previous = self._last_proc.get(pid)
            if previous is not None:
                last_ticks, last_total, _ = previous
                total_delta = total_jiffies - last_total
                proc_delta = ticks - last_ticks
                if total_delta > 0 and proc_delta >= 0:
                    cpu_percent = proc_delta / total_delta * self._cpu_count * 100.0
            self._last_proc[pid] = (ticks, float(total_jiffies), label)
            rows.append(
                {
                    "pid": pid,
                    "name": label,
                    "cpu_percent": round(cpu_percent, 1) if cpu_percent is not None else None,
                    "rss_mb": round(rss_kb / 1024.0, 1),
                    "command": command,
                }
            )

        for pid in list(self._last_proc):
            if pid not in seen:
                self._last_proc.pop(pid, None)
        rows.sort(key=lambda item: item["cpu_percent"] if item["cpu_percent"] is not None else -1, reverse=True)
        return rows


def read_cpu_total() -> tuple[int, int]:
    fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
    values = [int(value) for value in fields[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def cpu_usage_percent(previous: tuple[int, int], current: tuple[int, int]) -> float | None:
    last_total, last_idle = previous
    total, idle = current
    total_delta = total - last_total
    idle_delta = idle - last_idle
    if total_delta <= 0:
        return None
    return max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))


def read_memory() -> dict[str, Any]:
    data: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, raw = line.split(":", 1)
        data[key] = int(raw.strip().split()[0])
    total = data.get("MemTotal", 0)
    available = data.get("MemAvailable", 0)
    used = max(0, total - available)
    percent = used / total * 100.0 if total else 0.0
    return {
        "total_mb": round(total / 1024.0, 1),
        "used_mb": round(used / 1024.0, 1),
        "available_mb": round(available / 1024.0, 1),
        "percent": round(percent, 1),
    }


def read_loadavg() -> list[float]:
    values = Path("/proc/loadavg").read_text(encoding="utf-8").split()[:3]
    return [round(float(value), 2) for value in values]


def read_temperature() -> float | None:
    for path in sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp")):
        try:
            raw = int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        if raw > 1000:
            return round(raw / 1000.0, 1)
        return round(float(raw), 1)
    return None


def find_target_processes() -> list[tuple[int, str, str]]:
    processes = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        pid = int(path.name)
        try:
            raw = (path / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        command = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
        if "ffmpeg" in command and "pipe:1" in command and "image2pipe" in command:
            processes.append((pid, "ffmpeg", command))
        elif (
            "python3 main.py" in command
            or any(part == "main.py" or part.endswith("/main.py") for part in command.split())
        ):
            processes.append((pid, "main.py", command))
    return processes


def read_process_stat(pid: int) -> tuple[int, int]:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    after_comm = stat.rsplit(")", 1)[1].strip().split()
    utime = int(after_comm[11])
    stime = int(after_comm[12])
    rss_pages = int(after_comm[21])
    page_size_kb = os.sysconf("SC_PAGE_SIZE") // 1024
    return utime + stime, rss_pages * page_size_kb


def unavailable_snapshot(reason: str) -> dict[str, Any]:
    return {
        "updated_at": time.time(),
        "available": False,
        "error": reason,
        "cpu_percent": None,
        "cpu_count": os.cpu_count() or 1,
        "loadavg": [],
        "memory": {},
        "temperature_c": None,
        "processes": [],
    }
