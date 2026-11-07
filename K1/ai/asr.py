"""Offline Chinese ASR for the K1 board: Silero VAD + SenseVoice-Small (int8 ONNX).

Only numpy + onnxruntime are required, both already present on Bianbu, so no
riscv64 native build (sherpa-onnx, kaldi-native-fbank, torch) is needed.

Model files (see scripts/install_asr_models.sh):
  models/asr/sense-voice/model.int8.onnx   sherpa-onnx export of SenseVoice-Small
  models/asr/sense-voice/tokens.txt
  models/asr/silero_vad.onnx
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import numpy as np

from ai.runtime import CPU_PROVIDER, ordered_providers

SAMPLE_RATE = 16000
FBANK_BINS = 80
FRAME_LENGTH = 400  # 25 ms
FRAME_SHIFT = 160  # 10 ms
FFT_SIZE = 512
PREEMPH = 0.97
FLT_EPSILON = float(np.finfo(np.float32).eps)

TAG_PATTERN = re.compile(r"<\|([^|]*)\|>")
EVENT_TAGS = {"Speech", "BGM", "Applause", "Laughter", "Cry", "Sneeze", "Breath", "Cough"}
EMOTION_TAGS = {"HAPPY", "SAD", "ANGRY", "NEUTRAL", "FEARFUL", "DISGUSTED", "SURPRISED", "EMO_UNKNOWN"}
LANGUAGE_TAGS = {"zh", "en", "yue", "ja", "ko", "nospeech"}


# --------------------------------------------------------------------------- fbank


def _mel(freq: np.ndarray | float) -> np.ndarray | float:
    return 1127.0 * np.log(1.0 + np.asarray(freq, dtype=np.float64) / 700.0)


def _mel_banks(num_bins: int = FBANK_BINS, sample_rate: int = SAMPLE_RATE, fft_size: int = FFT_SIZE) -> np.ndarray:
    """Kaldi MelBanks (low_freq=20, high_freq=nyquist, no VTLN); shape (num_bins, fft_size // 2 + 1)."""
    num_fft_bins = fft_size // 2
    fft_bin_width = sample_rate / fft_size
    mel_low = _mel(20.0)
    mel_high = _mel(sample_rate / 2.0)
    delta = (mel_high - mel_low) / (num_bins + 1)
    mels = _mel(np.arange(num_fft_bins) * fft_bin_width)
    banks = np.zeros((num_bins, num_fft_bins + 1), dtype=np.float32)
    for index in range(num_bins):
        left = mel_low + index * delta
        center = left + delta
        right = center + delta
        up = (mels - left) / (center - left)
        down = (right - mels) / (right - center)
        weights = np.where(mels <= center, up, down)
        inside = (mels > left) & (mels < right)
        banks[index, :num_fft_bins] = np.where(inside, weights, 0.0)
    return banks


_MEL_BANKS = _mel_banks()
_POVEY_WINDOW = np.power(
    0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(FRAME_LENGTH) / (FRAME_LENGTH - 1)), 0.85
).astype(np.float32)


def kaldi_fbank(samples: np.ndarray) -> np.ndarray:
    """80-dim log-mel fbank matching kaldi-native-fbank defaults with dither=0, snip_edges=False.

    ``samples`` are 16 kHz mono floats in int16 scale (i.e. already multiplied by 32768).
    Returns float32 array of shape (num_frames, 80).
    """
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    count = samples.size
    num_frames = (count + FRAME_SHIFT // 2) // FRAME_SHIFT if count else 0
    if num_frames == 0:
        return np.zeros((0, FBANK_BINS), dtype=np.float32)
    starts = np.arange(num_frames) * FRAME_SHIFT + FRAME_SHIFT // 2 - FRAME_LENGTH // 2
    indices = starts[:, None] + np.arange(FRAME_LENGTH)[None, :]
    # Kaldi reflects out-of-range sample indices back into the waveform.
    while True:
        low = indices < 0
        high = indices >= count
        if not (low.any() or high.any()):
            break
        indices = np.where(low, -indices - 1, indices)
        indices = np.where(high, 2 * count - 1 - indices, indices)
    frames = samples[indices].astype(np.float32)
    frames -= frames.mean(axis=1, keepdims=True)
    emphasized = frames.copy()
    emphasized[:, 1:] -= PREEMPH * frames[:, :-1]
    emphasized[:, 0] -= PREEMPH * frames[:, 0]
    emphasized *= _POVEY_WINDOW
    spectrum = np.fft.rfft(emphasized, n=FFT_SIZE, axis=1)
    power = (spectrum.real ** 2 + spectrum.imag ** 2).astype(np.float32)
    energies = power @ _MEL_BANKS.T
    return np.log(np.maximum(energies, FLT_EPSILON)).astype(np.float32)


def apply_lfr(features: np.ndarray, window_size: int = 7, window_shift: int = 6) -> np.ndarray:
    """Low frame rate stacking used by SenseVoice/Paraformer (no padding, like sherpa-onnx)."""
    frames, dim = features.shape
    out_frames = (frames - window_size) // window_shift + 1
    if out_frames <= 0:
        return np.zeros((0, dim * window_size), dtype=np.float32)
    index = np.arange(out_frames)[:, None] * window_shift + np.arange(window_size)[None, :]
    return features[index].reshape(out_frames, dim * window_size).astype(np.float32)


# --------------------------------------------------------------------------- helpers


def _session_options(threads: int):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = max(1, int(threads))
    options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return options


def _create_session(model_path: Path, provider: str | None, threads: int):
    import onnxruntime as ort

    providers = ordered_providers(provider or CPU_PROVIDER, fallback=True)
    return ort.InferenceSession(str(model_path), sess_options=_session_options(threads), providers=providers)


def _parse_floats(value: str | None) -> np.ndarray | None:
    if not value:
        return None
    return np.array([float(item) for item in value.split(",") if item.strip()], dtype=np.float32)


def load_tokens(path: Path) -> list[str]:
    tokens: dict[int, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.rsplit(maxsplit=1)
        if len(parts) == 1:
            # A token that is itself whitespace-only cannot happen in SenseVoice, but be safe.
            tokens[int(parts[0])] = " "
            continue
        tokens[int(parts[1])] = parts[0]
    size = max(tokens) + 1 if tokens else 0
    return [tokens.get(index, "") for index in range(size)]


def split_tags(raw: str) -> tuple[str, dict[str, str]]:
    tags: dict[str, str] = {}
    for tag in TAG_PATTERN.findall(raw):
        if tag in LANGUAGE_TAGS:
            tags.setdefault("language", tag)
        elif tag in EMOTION_TAGS:
            tags.setdefault("emotion", tag)
        elif tag in EVENT_TAGS:
            tags.setdefault("event", tag)
        elif tag in {"withitn", "woitn"}:
            tags.setdefault("itn", tag)
    text = TAG_PATTERN.sub("", raw).replace("▁", " ").strip()
    return text, tags


# --------------------------------------------------------------------------- SenseVoice


class SenseVoiceRecognizer:
    """Non-streaming SenseVoice-Small (sherpa-onnx ONNX export) with CTC greedy decoding."""

    def __init__(
        self,
        model_path: str | Path,
        tokens_path: str | Path,
        language: str = "auto",
        use_itn: bool = True,
        provider: str | None = CPU_PROVIDER,
        threads: int = 4,
    ) -> None:
        self.model_path = Path(model_path)
        self.tokens = load_tokens(Path(tokens_path))
        self.session = _create_session(self.model_path, provider, threads)
        self.provider = self.session.get_providers()[0]
        meta = self.session.get_modelmeta().custom_metadata_map
        self.window_size = int(meta.get("lfr_window_size", 7))
        self.window_shift = int(meta.get("lfr_window_shift", 6))
        self.neg_mean = _parse_floats(meta.get("neg_mean"))
        self.inv_stddev = _parse_floats(meta.get("inv_stddev"))
        self.normalize_samples = str(meta.get("normalize_samples", "0")) not in {"0", "false", "False"}
        self.blank_id = int(meta.get("blank_id", 0))
        language = (language or "auto").strip().lower()
        self.language = language
        self.language_id = int(meta.get(f"lang_{language}", meta.get("lang_auto", 0)))
        self.text_norm_id = int(meta.get("with_itn", 14) if use_itn else meta.get("without_itn", 15))
        self.input_names = [item.name for item in self.session.get_inputs()]
        self.output_name = self.session.get_outputs()[0].name

    def features(self, waveform: np.ndarray) -> np.ndarray:
        samples = np.asarray(waveform, dtype=np.float32)
        if not self.normalize_samples:
            samples = samples * 32768.0
        feats = apply_lfr(kaldi_fbank(samples), self.window_size, self.window_shift)
        if self.neg_mean is not None and self.inv_stddev is not None and feats.size:
            feats = (feats + self.neg_mean) * self.inv_stddev
        return feats.astype(np.float32)

    def transcribe(self, waveform: np.ndarray, sample_rate: int = SAMPLE_RATE) -> dict[str, Any]:
        """``waveform`` is mono float32 in [-1, 1]."""
        if sample_rate != SAMPLE_RATE:
            raise ValueError(f"SenseVoice expects {SAMPLE_RATE} Hz audio, got {sample_rate}")
        started = time.perf_counter()
        feats = self.features(waveform)
        duration_s = round(float(np.asarray(waveform).size) / SAMPLE_RATE, 3)
        if feats.shape[0] == 0:
            return {"text": "", "raw": "", "tags": {}, "elapsed_ms": 0.0, "audio_s": duration_s}
        inputs = {
            self.input_names[0]: feats[None, :, :],
            self.input_names[1]: np.array([feats.shape[0]], dtype=np.int32),
            self.input_names[2]: np.array([self.language_id], dtype=np.int32),
            self.input_names[3]: np.array([self.text_norm_id], dtype=np.int32),
        }
        logits = self.session.run([self.output_name], inputs)[0][0]
        ids = logits.argmax(axis=-1)
        keep = np.ones(ids.shape, dtype=bool)
        keep[1:] = ids[1:] != ids[:-1]
        ids = ids[keep]
        raw = "".join(self.tokens[int(index)] for index in ids if int(index) != self.blank_id and int(index) < len(self.tokens))
        text, tags = split_tags(raw)
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
        return {
            "text": text,
            "raw": raw,
            "tags": tags,
            "elapsed_ms": elapsed_ms,
            "audio_s": duration_s,
            "rtf": round(elapsed_ms / 1000.0 / max(duration_s, 1e-3), 3),
        }


# --------------------------------------------------------------------------- Silero VAD


class SileroVad:
    """Silero VAD ONNX (v4 h/c state or v5 single state); 512-sample windows at 16 kHz."""

    WINDOW = 512
    CONTEXT = 64

    def __init__(self, model_path: str | Path, threads: int = 1) -> None:
        self.session = _create_session(Path(model_path), CPU_PROVIDER, threads)
        names = [item.name for item in self.session.get_inputs()]
        self.version = 5 if "state" in names else 4
        # Official exports call the audio input "input" and take "sr"; the sherpa-onnx
        # export uses "x" with no sample-rate input. Detect instead of hard-coding.
        self.data_name = next(name for name in names if name not in {"state", "h", "c", "sr"})
        self.has_sr = "sr" in names
        self.reset()

    def reset(self) -> None:
        if self.version == 5:
            self._state = np.zeros((2, 1, 128), dtype=np.float32)
            self._context = np.zeros(self.CONTEXT, dtype=np.float32)
        else:
            self._h = np.zeros((2, 1, 64), dtype=np.float32)
            self._c = np.zeros((2, 1, 64), dtype=np.float32)

    def __call__(self, window: np.ndarray) -> float:
        window = np.asarray(window, dtype=np.float32).reshape(-1)
        if window.size != self.WINDOW:
            raise ValueError(f"Silero VAD expects {self.WINDOW} samples, got {window.size}")
        feeds: dict[str, np.ndarray] = {}
        if self.has_sr:
            feeds["sr"] = np.array(SAMPLE_RATE, dtype=np.int64)
        if self.version == 5:
            feeds[self.data_name] = np.concatenate([self._context, window])[None, :]
            feeds["state"] = self._state
            prob, self._state = self.session.run(None, feeds)[:2]
            self._context = window[-self.CONTEXT:].copy()
        else:
            feeds.update({self.data_name: window[None, :], "h": self._h, "c": self._c})
            prob, self._h, self._c = self.session.run(None, feeds)[:3]
        return float(np.asarray(prob).reshape(-1)[0])
