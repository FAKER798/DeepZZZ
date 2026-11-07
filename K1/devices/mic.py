from __future__ import annotations

import subprocess
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np


def record_audio_level(output: Path, seconds: float, device: str = "plughw:0,0", sample_rate: int = 16000) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "arecord",
        "-D",
        device,
        "-f",
        "S16_LE",
        "-r",
        str(sample_rate),
        "-c",
        "1",
        "-d",
        str(max(1, int(round(seconds)))),
        str(output),
    ]
    started = time.perf_counter()
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=seconds + 5)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "arecord failed").strip())

    samples, rate = read_pcm16(output)
    if samples.size == 0:
        rms = peak = zero_crossing_rate = 0.0
    else:
        normalized = samples.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(normalized * normalized)))
        peak = float(np.max(np.abs(normalized)))
        signs = np.signbit(normalized)
        zero_crossing_rate = float(np.count_nonzero(signs[1:] != signs[:-1]) / max(1, len(signs) - 1))

    dbfs = -120.0 if rms <= 1e-8 else 20.0 * float(np.log10(rms))
    return {
        "file": str(output),
        "audio_url": f"/audio/{output.name}",
        "sample_rate": rate,
        "duration_s": round(samples.size / max(1, rate), 3),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "rms": round(rms, 5),
        "peak": round(peak, 5),
        "dbfs": round(dbfs, 2),
        "zero_crossing_rate": round(zero_crossing_rate, 5),
        "engine": "arecord_level_meter",
    }


def read_pcm16(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    samples = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return samples, rate
