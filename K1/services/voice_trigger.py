"""Voice care-log worker: "换尿布" / "喂奶" / "宝宝睡着了" -> care records.

Runs as its own systemd service (scripts/voice-trigger.service), pinned to the
CPU cores the main service does not use. It never opens the microphone itself:

    main.py /api/audio/live.pcm (16 kHz s16le mono, shared arecord)
      -> SileroVad speech segmentation
      -> SenseVoice-Small ASR
      -> CareIntentMatcher (diaper / feeding / sleep start / sleep end)
      -> POST /api/care/records on main.py  (same store as the naviga app Care page)
      -> runtime/state/voice_trigger.json  (read by main.py /api/status)

Repeated phrases are merged: a second "换尿布" within diaper_merge_s updates the
record the worker created instead of logging a new diaper change.
The main service toggles the worker through runtime/state/voice_trigger_command.json.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, dataclass, field, replace
import json
import os
import queue
import re
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from k1edge.runtime_store import age_seconds, read_json, write_json  # noqa: E402
from utils.paths import (  # noqa: E402
    ASR_MODEL_DIR,
    PROJECT_ROOT as _PROJECT_ROOT,
    VOICE_SEGMENT_DIR,
    VOICE_PAUSE_FILE,
    VOICE_TRIGGER_COMMAND_FILE,
    VOICE_TRIGGER_STATUS_FILE,
)

SAMPLE_RATE = 16000
VAD_WINDOW = 512
STATUS_STALE_S = 6.0
HISTORY_SIZE = 12
MAX_SLEEP_S = 24 * 3600.0

# ---- keyword lists (normalized: simplified Chinese, no punctuation/spaces)
DEFAULT_DIAPER_KEYWORDS = (
    "换尿布", "换尿不湿", "换纸尿裤", "换尿片", "换片片", "尿布", "尿不湿", "纸尿裤", "尿片", "拉拉裤",
)
DEFAULT_FEEDING_KEYWORDS = (
    "喂奶", "吃奶", "喝奶", "冲奶", "泡奶", "喂母乳", "吃母乳", "亲喂", "瓶喂", "喂配方奶", "喝配方奶",
    "喂辅食", "吃辅食", "喂米粉", "吃米粉", "喂饭", "吃饭饭", "喂米糊",
)
DEFAULT_SLEEP_START_KEYWORDS = (
    "睡着了", "哄睡了", "哄睡好了", "哄睡着了", "入睡了", "宝宝睡了", "已经睡了",
    "宝宝睡觉了", "开始睡觉", "睡着啦", "睡觉啦",
)
DEFAULT_SLEEP_END_KEYWORDS = ("醒了", "睡醒了", "醒来了", "醒啦", "醒来啦", "睡醒啦", "起床了")
DEFAULT_CARE_TYPES = ("diaper", "feeding", "sleep")

# A phrase containing one of these is about the future / a refusal, not something that happened.
NEGATIONS = {
    "diaper": (
        "不用换", "不要换", "不换", "没换", "别换", "还没换", "要不要换", "该不该换",
        "待会换", "等会换", "一会换", "等下换", "晚点换", "待会再换", "等会再换", "一会再换", "等下再换", "晚点再换",
    ),
    "feeding": (
        "不用喂", "不要喂", "不喂", "没喂", "别喂", "还没喂", "还没吃", "不吃", "不喝", "没吃", "没喝",
        "不肯吃", "要不要喂", "该不该喂",
        "待会喂", "等会喂", "一会喂", "等下喂", "晚点喂", "待会再喂", "等会再喂", "一会再喂", "等下再喂", "晚点再喂",
    ),
    "sleep_start": (
        "没睡着", "睡不着", "还没睡", "不睡", "没睡", "别睡", "快睡着", "要睡着",
        "该睡觉", "要睡觉", "想睡觉", "去睡觉", "准备睡觉", "睡觉吧", "睡觉了吗", "开始睡觉吧",
    ),
    "sleep_end": ("没醒", "别吵醒", "不要吵醒", "会吵醒", "怕吵醒", "快醒", "要醒", "不醒", "别醒", "会醒", "醒了没", "醒了吗"),
}
# Only clear yes/no questions are skipped. Sentences ending in modal particles
# (呢 吧 啊 呀 啦 哦 嘛 ...) are still logged: "宝宝喂奶粉呢" reports a feeding.
QUESTION_ENDINGS = ("吗", "么", "了没", "没有", "没呀", "好不好", "要不要")
DIAPER_DIRTY_WORDS = ("便便", "大便", "拉屎", "粑粑", "臭臭", "拉臭", "拉了", "屎")
DIAPER_WET_WORDS = ("尿尿", "嘘嘘", "小便", "尿了", "尿湿", "湿了", "好多尿", "一泡尿", "湿湿")
DIAPER_DRY_WORDS = ("干的", "干爽", "没尿", "没有尿", "是干")
SOLID_WORDS = ("辅食", "米粉", "米糊", "果泥", "菜泥", "肉泥", "蛋黄", "喂饭", "吃饭饭", "粥")
BOTTLE_WORDS = ("瓶喂", "奶瓶", "冲奶", "泡奶", "奶粉", "配方奶")
FORMULA_WORDS = ("奶粉", "配方奶", "冲奶", "泡奶")
BREAST_WORDS = ("亲喂", "喂母乳", "吃母乳", "妈妈的奶", "吃妈妈")
LEFT_WORDS = ("左边", "左侧", "左面")
RIGHT_WORDS = ("右边", "右侧", "右面")
BOTH_WORDS = ("两边", "两侧", "双侧", "左右")

# Traditional -> simplified for the characters used by the keyword lists, so a
# Cantonese (yue) transcript such as "換尿片" still matches.
_TRADITIONAL = str.maketrans({
    "換": "换", "紙": "纸", "褲": "裤", "濕": "湿", "餵": "喂", "飯": "饭", "醒": "醒", "睡": "睡",
    "哄": "哄", "邊": "边", "側": "侧", "兩": "两", "瓶": "瓶", "親": "亲", "媽": "妈", "裡": "里",
    "沒": "没", "別": "别", "會": "会", "還": "还", "給": "给", "個": "个", "來": "来", "後": "后",
    "糊": "糊", "蛋": "蛋", "黃": "黄", "臭": "臭", "髒": "脏", "乾": "干", "噓": "嘘", "鐘": "钟",
    "毫": "毫", "昇": "升", "糞": "粪", "屎": "屎", "準": "准", "備": "备",
})
_NON_WORD = re.compile(r"[^0-9a-z一-鿿]+")
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_NUM = r"(\d+|[零一二两三四五六七八九十百]+)"
_AMOUNT_RE = re.compile(_NUM + r"(?:毫升|ml|cc|毫)")
_DURATION_RE = re.compile(_NUM + r"分钟")


# --------------------------------------------------------------------------- settings


@dataclass(frozen=True)
class VoiceTriggerSettings:
    base_url: str = "http://127.0.0.1:7860"
    enabled: bool = True
    model_dir: str = str(ASR_MODEL_DIR / "sense-voice")
    vad_model: str = str(ASR_MODEL_DIR / "silero_vad.onnx")
    language: str = "auto"
    use_itn: bool = True
    provider: str = "CPUExecutionProvider"
    threads: int = 4
    care_types: tuple[str, ...] = DEFAULT_CARE_TYPES
    diaper_keywords: tuple[str, ...] = DEFAULT_DIAPER_KEYWORDS
    feeding_keywords: tuple[str, ...] = DEFAULT_FEEDING_KEYWORDS
    sleep_start_keywords: tuple[str, ...] = DEFAULT_SLEEP_START_KEYWORDS
    sleep_end_keywords: tuple[str, ...] = DEFAULT_SLEEP_END_KEYWORDS
    fuzzy_pinyin: bool = True  # also match keywords (3+ chars) by pronunciation: "尿不时" -> "尿不湿"
    fuzzy_threshold: float = 0.9
    pinyin_table: str = ""  # "" = models/asr/py_table.mb, then fcitx5's copy
    diaper_merge_s: float = 600.0  # same diaper change if mentioned again within 10 min
    feeding_merge_s: float = 1800.0  # same feeding session within 30 min
    vad_threshold: float = 0.5
    min_speech_s: float = 0.3
    min_silence_s: float = 0.5
    max_speech_s: float = 8.0
    pre_roll_s: float = 0.3
    save_segments: bool = False
    status_path: str = str(VOICE_TRIGGER_STATUS_FILE)
    command_path: str = str(VOICE_TRIGGER_COMMAND_FILE)
    pause_path: str = str(VOICE_PAUSE_FILE)

    def keyword_settings(self) -> dict[str, Any]:
        return {
            "care_types": list(self.care_types),
            "diaper_keywords": list(self.diaper_keywords),
            "feeding_keywords": list(self.feeding_keywords),
            "sleep_start_keywords": list(self.sleep_start_keywords),
            "sleep_end_keywords": list(self.sleep_end_keywords),
            "fuzzy_pinyin": self.fuzzy_pinyin,
            "fuzzy_threshold": self.fuzzy_threshold,
            "diaper_merge_s": self.diaper_merge_s,
            "feeding_merge_s": self.feeding_merge_s,
        }


_LIST_KEYS = {"care_types", "diaper_keywords", "feeding_keywords", "sleep_start_keywords", "sleep_end_keywords"}


def _as_keywords(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        items: Iterable[Any] = re.split(r"[,，;；\s]+", value)
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        return ()
    return tuple(word for word in (normalize_text(str(item)) for item in items) if word)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def settings_from_mapping(base: VoiceTriggerSettings, values: dict[str, Any]) -> VoiceTriggerSettings:
    updates: dict[str, Any] = {}
    for key, default in asdict(base).items():
        if key not in values or values[key] is None:
            continue
        raw = values[key]
        if key in _LIST_KEYS:
            keywords = _as_keywords(raw)
            if keywords or key == "care_types":
                updates[key] = keywords
        elif isinstance(default, bool):
            updates[key] = _as_bool(raw)
        elif isinstance(default, int):
            updates[key] = int(raw)
        elif isinstance(default, float):
            updates[key] = float(raw)
        else:
            updates[key] = str(raw)
    return replace(base, **updates)


def load_settings(argv: list[str] | None = None, config_path: Path | None = None) -> tuple[VoiceTriggerSettings, argparse.Namespace]:
    """defaults -> config.json["voice_trigger"] -> VOICE_TRIGGER_<KEY> env -> CLI flags."""
    settings = VoiceTriggerSettings()
    config_path = config_path or (_PROJECT_ROOT / "config.json")
    try:
        file_values = json.loads(config_path.read_text(encoding="utf-8")).get("voice_trigger") or {}
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        file_values = {}
    if isinstance(file_values, dict):
        settings = settings_from_mapping(settings, file_values)
    env_values = {
        key: os.environ[f"VOICE_TRIGGER_{key.upper()}"]
        for key in asdict(settings)
        if f"VOICE_TRIGGER_{key.upper()}" in os.environ
    }
    settings = settings_from_mapping(settings, env_values)

    parser = argparse.ArgumentParser(description="DeepZZZ voice care-log worker")
    parser.add_argument("--base-url")
    parser.add_argument("--model-dir")
    parser.add_argument("--vad-model")
    parser.add_argument("--language")
    parser.add_argument("--provider")
    parser.add_argument("--threads", type=int)
    parser.add_argument("--save-segments", action="store_true", default=None)
    parser.add_argument("--test-wav", nargs="+", help="transcribe wav file(s), print the matched care events and exit")
    parser.add_argument("--test-text", nargs="+", help="print the care events matched in the given sentence(s) and exit")
    parser.add_argument("--benchmark", type=int, default=0, help="with --test-wav: repeat each file N times")
    args = parser.parse_args(argv)
    cli_values = {key: value for key, value in vars(args).items() if key not in {"test_wav", "test_text", "benchmark"}}
    settings = settings_from_mapping(settings, cli_values)
    return settings, args


# --------------------------------------------------------------------------- matching


def normalize_text(text: str) -> str:
    return _NON_WORD.sub("", str(text).lower().translate(_TRADITIONAL))


def chinese_number(text: str) -> int | None:
    """'120' / '一百二十' / '一百五' / '九十' / '两百' -> int (0..9999)."""
    if text.isdigit():
        return int(text)
    total, current, last_unit = 0, 0, 1
    for char in text:
        if char == "零":
            current, last_unit = 0, 10  # "一百零五": the next digit is a unit digit
        elif char in _CN_DIGITS:
            current = _CN_DIGITS[char]
        elif char == "十":
            total += (current or 1) * 10
            current, last_unit = 0, 10
        elif char == "百":
            total += (current or 1) * 100
            current, last_unit = 0, 100
        else:
            return None
    if current:
        # "一百五" means 150, "三十五" means 35.
        total += current * (last_unit // 10 if last_unit >= 100 else 1)
    return total or None


def _contains(text: str, words: Iterable[str]) -> str | None:
    return next((word for word in words if word and word in text), None)


@dataclass
class CareEvent:
    type: str  # diaper | feeding | sleep
    action: str  # log | sleep_start | sleep_end
    keyword: str
    fields: dict[str, Any] = field(default_factory=dict)


class CareIntentMatcher:
    def __init__(self, settings: VoiceTriggerSettings, pinyin: Any = None) -> None:
        self.types = set(settings.care_types)
        self.diaper = tuple(normalize_text(word) for word in settings.diaper_keywords)
        self.feeding = tuple(normalize_text(word) for word in settings.feeding_keywords)
        self.sleep_start = tuple(normalize_text(word) for word in settings.sleep_start_keywords)
        self.sleep_end = tuple(normalize_text(word) for word in settings.sleep_end_keywords)
        if pinyin is None and settings.fuzzy_pinyin:
            from services.pinyin_match import PinyinMatcher, default_table

            pinyin = PinyinMatcher(default_table(settings.pinyin_table), threshold=settings.fuzzy_threshold)
        self.pinyin = pinyin if pinyin is not None and getattr(pinyin, "available", False) else None

    def _find(self, normalized: str, keywords: tuple[str, ...]) -> str | None:
        """Exact keyword first, then a pronunciation match shown as "尿不湿≈尿不时"."""
        keyword = _contains(normalized, keywords)
        if keyword or self.pinyin is None:
            return keyword
        hit = self.pinyin.find(normalized, keywords)
        return f"{hit[0]}≈{hit[1]}" if hit else None

    def match(self, text: str) -> list[CareEvent]:
        normalized = normalize_text(text)
        if not normalized or is_question(normalized):
            return []
        events: list[CareEvent] = []
        if "diaper" in self.types:
            keyword = self._find(normalized, self.diaper)
            if keyword and not _contains(normalized, NEGATIONS["diaper"]):
                events.append(CareEvent("diaper", "log", keyword, diaper_fields(normalized)))
        if "feeding" in self.types:
            keyword = self._find(normalized, self.feeding)
            if keyword and not _contains(normalized, NEGATIONS["feeding"]):
                events.append(CareEvent("feeding", "log", keyword, feeding_fields(normalized)))
        if "sleep" in self.types:
            start = self._find(normalized, self.sleep_start)
            end = self._find(normalized, self.sleep_end)
            if start and not end and not _contains(normalized, NEGATIONS["sleep_start"]):
                events.append(CareEvent("sleep", "sleep_start", start))
            elif end and not start and not _contains(normalized, NEGATIONS["sleep_end"]):
                events.append(CareEvent("sleep", "sleep_end", end))
        return events


def diaper_fields(normalized: str) -> dict[str, Any]:
    dirty = _contains(normalized, DIAPER_DIRTY_WORDS)
    wet = _contains(normalized, DIAPER_WET_WORDS)
    if dirty and wet:
        return {"kind": "mixed"}
    if dirty:
        return {"kind": "dirty"}
    if wet:
        return {"kind": "wet"}
    if _contains(normalized, DIAPER_DRY_WORDS):
        return {"kind": "dry"}
    return {}


def feeding_fields(normalized: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    amount_match = _AMOUNT_RE.search(normalized)
    amount = chinese_number(amount_match.group(1)) if amount_match else None
    duration_match = _DURATION_RE.search(normalized)
    duration = chinese_number(duration_match.group(1)) if duration_match else None
    left, right = _contains(normalized, LEFT_WORDS), _contains(normalized, RIGHT_WORDS)
    side = "both" if (left and right) or _contains(normalized, BOTH_WORDS) else "left" if left else "right" if right else None

    solid = _contains(normalized, SOLID_WORDS)
    if solid:
        fields["kind"] = "solid"
        fields["food"] = {"喂饭": "饭", "吃饭饭": "饭"}.get(solid, solid)
    elif _contains(normalized, BOTTLE_WORDS) or amount is not None:
        fields["kind"] = "bottle"
        if _contains(normalized, FORMULA_WORDS):
            fields["milk"] = "formula"
        elif "母乳" in normalized:
            fields["milk"] = "breast_milk"
    elif _contains(normalized, BREAST_WORDS) or side:
        fields["kind"] = "breast"
    if amount is not None and 0 < amount <= 500 and fields.get("kind") != "solid":
        fields["amount_ml"] = amount
    if duration is not None and 0 < duration <= 240 and fields.get("kind") == "breast":
        fields["duration_min"] = duration
    if side and fields.get("kind") == "breast":
        fields["side"] = side
    return fields


def compatible_kind(record: dict[str, Any], fields: dict[str, Any]) -> bool:
    """Feedings merge only when the kinds agree (or one side is unknown); diaper kinds combine."""
    if record.get("type") != "feeding":
        return True
    old, new = record.get("kind"), fields.get("kind")
    if old and new and old != new:
        return False
    old_milk, new_milk = record.get("milk"), fields.get("milk")
    return not (old_milk and new_milk and old_milk != new_milk)


def is_question(normalized: str) -> bool:
    """'宝宝喂母乳吗' / '喂奶了没' ask about a feeding rather than report one."""
    return normalized.endswith(QUESTION_ENDINGS)


def merge_fields(record: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    """Fields to add to an existing voice record (never overwrites what is already there)."""
    updates: dict[str, Any] = {}
    for key, value in fields.items():
        old = record.get(key)
        if key == "kind" and record.get("type") == "diaper" and {old, value} == {"wet", "dirty"}:
            updates[key] = "mixed"
        elif old in (None, "") and value not in (None, ""):
            updates[key] = value
    return updates


# --------------------------------------------------------------------------- segmentation


class SpeechSegmenter:
    """Turns a stream of 16 kHz float samples into speech segments using a VAD callable."""

    def __init__(
        self,
        vad: Callable[[np.ndarray], float],
        threshold: float = 0.5,
        min_speech_s: float = 0.3,
        min_silence_s: float = 0.5,
        max_speech_s: float = 8.0,
        pre_roll_s: float = 0.3,
        window: int = VAD_WINDOW,
    ) -> None:
        self.vad = vad
        self.threshold = float(threshold)
        self.neg_threshold = max(0.05, self.threshold - 0.15)
        self.window = int(window)
        per_s = SAMPLE_RATE / self.window
        self.min_speech_windows = max(1, int(round(min_speech_s * per_s)))
        self.min_silence_windows = max(1, int(round(min_silence_s * per_s)))
        self.max_speech_windows = max(self.min_speech_windows + 1, int(round(max_speech_s * per_s)))
        self.pre_roll: deque[np.ndarray] = deque(maxlen=max(0, int(round(pre_roll_s * per_s))))
        self._pending = np.zeros(0, dtype=np.float32)
        self._segment: list[np.ndarray] = []
        self._speech_windows = 0
        self._silence_windows = 0
        self.in_speech = False
        self.last_probability = 0.0

    def reset(self) -> None:
        self._pending = np.zeros(0, dtype=np.float32)
        self._segment = []
        self._speech_windows = 0
        self._silence_windows = 0
        self.in_speech = False
        self.pre_roll.clear()
        reset = getattr(self.vad, "reset", None)
        if callable(reset):
            reset()

    def feed(self, samples: np.ndarray) -> list[np.ndarray]:
        data = np.concatenate([self._pending, np.asarray(samples, dtype=np.float32).reshape(-1)])
        usable = data.size - data.size % self.window
        self._pending = data[usable:]
        segments: list[np.ndarray] = []
        for offset in range(0, usable, self.window):
            segment = self._step(data[offset : offset + self.window])
            if segment is not None:
                segments.append(segment)
        return segments

    def _step(self, window: np.ndarray) -> np.ndarray | None:
        probability = float(self.vad(window))
        self.last_probability = probability
        if not self.in_speech:
            if probability >= self.threshold:
                self.in_speech = True
                self._segment = list(self.pre_roll) + [window]
                self._speech_windows = 1
                self._silence_windows = 0
                self.pre_roll.clear()
            else:
                self.pre_roll.append(window)
            return None

        self._segment.append(window)
        if probability >= self.neg_threshold:
            self._speech_windows += 1
            self._silence_windows = 0
        else:
            self._silence_windows += 1

        if self._silence_windows >= self.min_silence_windows:
            return self._finish(trim=self._silence_windows - 2)
        if len(self._segment) >= self.max_speech_windows:
            segment = self._finish(trim=0)
            # Keep listening: a long sentence continues straight into the next segment.
            self.in_speech = probability >= self.neg_threshold
            return segment
        return None

    def _finish(self, trim: int) -> np.ndarray | None:
        windows = self._segment[: len(self._segment) - trim] if trim > 0 else self._segment
        speech_windows = self._speech_windows
        self._segment = []
        self._speech_windows = 0
        self._silence_windows = 0
        self.in_speech = False
        if speech_windows < self.min_speech_windows or not windows:
            return None
        return np.concatenate(windows)


# --------------------------------------------------------------------------- main-service client


class EdgeClient:
    def __init__(self, base_url: str, timeout: float = 3.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)

    def _request(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urlrequest.Request(
            f"{self.base_url}{path}",
            data=data,
            method="GET" if body is None else "POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlrequest.urlopen(req, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urlerror.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                payload = {}
            raise RuntimeError(payload.get("error") or f"HTTP {exc.code} for {path}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"unexpected response for {path}")
        if payload.get("ok") is False:
            raise RuntimeError(str(payload.get("error") or f"request failed: {path}"))
        return payload

    def list_care_records(self, record_type: str, since: float | None = None, limit: int = 5) -> list[dict[str, Any]]:
        query = {"type": record_type, "limit": limit}
        if since is not None:
            query["since"] = round(since, 3)
        result = self._request("/api/care/records?" + urlparse.urlencode(query)).get("result")
        return [item for item in result if isinstance(item, dict)] if isinstance(result, list) else []

    def create_care_record(self, payload: dict[str, Any]) -> dict[str, Any]:
        return dict(self._request("/api/care/records", payload).get("result") or {})

    def update_care_record(self, record_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return dict(self._request(f"/api/care/records/{urlparse.quote(record_id, safe='')}", payload).get("result") or {})

    def open_pcm(self):
        return urlrequest.urlopen(f"{self.base_url}/api/audio/live.pcm", timeout=5.0)


# --------------------------------------------------------------------------- worker


@dataclass
class _Counters:
    segments: int = 0
    dropped_segments: int = 0
    transcripts: int = 0
    records_logged: int = 0
    records_merged: int = 0
    events_skipped: int = 0
    asr_ms_total: float = 0.0
    asr_audio_s_total: float = 0.0


class VoiceTriggerWorker:
    def __init__(
        self,
        settings: VoiceTriggerSettings,
        client: EdgeClient | None = None,
        recognizer: Any = None,
        vad: Callable[[np.ndarray], float] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.client = client or EdgeClient(settings.base_url)
        self.recognizer = recognizer
        self.vad = vad
        self.clock = clock
        self.matcher = CareIntentMatcher(settings)
        self.status_path = Path(settings.status_path)
        self.command_path = Path(settings.command_path)
        self.pause_path = Path(settings.pause_path)
        self.paused_until: float | None = None
        self.pause_reason: str | None = None
        self.enabled = settings.enabled
        self.state = "starting"
        self.error: str | None = None
        self.connected = False
        self.segmenter: SpeechSegmenter | None = None
        self.history: deque[dict[str, Any]] = deque(maxlen=HISTORY_SIZE)
        self.recent_records: deque[dict[str, Any]] = deque(maxlen=HISTORY_SIZE)
        self.last_action: dict[str, Any] | None = None
        self.counters = _Counters()
        self._started_at = clock()
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._segments: queue.Queue[tuple[float, np.ndarray]] = queue.Queue(maxsize=4)
        self._command_mtime: float | None = None

    # ---------------------------------------------------------------- lifecycle

    def load_models(self) -> None:
        from ai.asr import SenseVoiceRecognizer, SileroVad

        model_dir = Path(self.settings.model_dir)
        model = model_dir / "model.int8.onnx"
        if not model.exists():
            model = model_dir / "model.onnx"
        tokens = model_dir / "tokens.txt"
        missing = [str(path) for path in (model, tokens, Path(self.settings.vad_model)) if not path.exists()]
        if missing:
            raise FileNotFoundError("ASR model files missing: " + ", ".join(missing) + " (run scripts/install_asr_models.sh)")
        self._set_state("loading")
        if self.vad is None:
            self.vad = SileroVad(self.settings.vad_model)
        if self.recognizer is None:
            self.recognizer = SenseVoiceRecognizer(
                model,
                tokens,
                language=self.settings.language,
                use_itn=self.settings.use_itn,
                provider=self.settings.provider,
                threads=self.settings.threads,
            )
            # Warm-up so the first real utterance is not slowed by lazy allocations.
            self.recognizer.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> int:
        signal.signal(signal.SIGTERM, lambda *_: self.stop())
        signal.signal(signal.SIGINT, lambda *_: self.stop())
        threading.Thread(target=self._housekeeping_loop, name="voice-housekeeping", daemon=True).start()
        try:
            self.load_models()
        except Exception as exc:
            self._set_state("error", str(exc))
            log(f"model load failed: {exc}")
            # Keep reporting the error to the web page instead of crash-looping.
            while not self._stop.wait(5.0):
                pass
            self._write_status()
            return 1
        self.segmenter = self._new_segmenter()
        threading.Thread(target=self._asr_loop, name="voice-asr", daemon=True).start()
        log(f"ready: provider={getattr(self.recognizer, 'provider', '?')} language={self.settings.language} care_types={','.join(self.settings.care_types)}")
        while not self._stop.is_set():
            if not self.enabled:
                self._set_state("disabled")
                self._stop.wait(0.5)
                continue
            if self.paused:
                self._set_state("paused")
                self._drop_queued_segments()
                self._stop.wait(0.2)
                continue
            try:
                self._stream_once()
            except Exception as exc:
                self.connected = False
                self._set_state("reconnecting", f"audio stream: {exc}")
                self._stop.wait(2.0)
        self._set_state("stopped")
        self._write_status()
        return 0

    def _new_segmenter(self) -> SpeechSegmenter:
        assert self.vad is not None
        return SpeechSegmenter(
            self.vad,
            threshold=self.settings.vad_threshold,
            min_speech_s=self.settings.min_speech_s,
            min_silence_s=self.settings.min_silence_s,
            max_speech_s=self.settings.max_speech_s,
            pre_roll_s=self.settings.pre_roll_s,
        )

    def _stream_once(self) -> None:
        assert self.segmenter is not None
        self.segmenter.reset()
        with self.client.open_pcm() as stream:
            self.connected = True
            self._set_state("listening")
            leftover = b""
            while not self._stop.is_set() and self.enabled and not self.paused:
                chunk = stream.read(VAD_WINDOW * 2)
                if not chunk:
                    raise RuntimeError("audio stream closed")
                chunk = leftover + chunk
                usable = len(chunk) - len(chunk) % 2
                leftover = chunk[usable:]
                samples = np.frombuffer(chunk[:usable], dtype=np.int16).astype(np.float32) / 32768.0
                self.feed_samples(samples)
        self.connected = False

    def feed_samples(self, samples: np.ndarray) -> None:
        assert self.segmenter is not None
        for segment in self.segmenter.feed(samples):
            self.counters.segments += 1
            try:
                self._segments.put_nowait((self.clock(), segment))
            except queue.Full:
                self.counters.dropped_segments += 1
                try:
                    self._segments.get_nowait()
                except queue.Empty:
                    pass
                self._segments.put_nowait((self.clock(), segment))
        if self.state in {"listening", "speech"}:
            self.state = "speech" if self.segmenter.in_speech else "listening"

    def _asr_loop(self) -> None:
        while not self._stop.is_set():
            try:
                captured_at, segment = self._segments.get(timeout=0.5)
            except queue.Empty:
                continue
            if self.paused:
                self.counters.dropped_segments += 1
                continue
            try:
                self.process_segment(segment, captured_at)
            except Exception as exc:
                self.error = f"asr: {exc}"
                log(self.error)

    # ---------------------------------------------------------------- decisions

    def process_segment(self, segment: np.ndarray, captured_at: float | None = None) -> dict[str, Any]:
        result = self.recognizer.transcribe(segment, SAMPLE_RATE)
        self.counters.transcripts += 1
        self.counters.asr_ms_total += float(result.get("elapsed_ms") or 0.0)
        self.counters.asr_audio_s_total += float(result.get("audio_s") or 0.0)
        if self.settings.save_segments:
            result["file"] = save_segment(segment)
        return self.handle_transcript(result, captured_at=captured_at)

    def handle_transcript(self, result: dict[str, Any], captured_at: float | None = None) -> dict[str, Any]:
        now = self.clock()
        text = str(result.get("text") or "").strip()
        meaningful = bool(normalize_text(text))  # drop "." / noise-only transcripts
        events = self.matcher.match(text) if meaningful else []
        entry: dict[str, Any] = {
            "at": now,
            "captured_at": captured_at,
            "text": text,
            "tags": result.get("tags") or {},
            "elapsed_ms": result.get("elapsed_ms"),
            "audio_s": result.get("audio_s"),
            "events": [],
        }
        for event in events:
            outcome = {"type": event.type, "action": event.action, "keyword": event.keyword, "fields": event.fields}
            try:
                outcome.update(self.apply_event(event, text, captured_at or now, now))
            except Exception as exc:
                outcome.update({"result": "error", "reason": str(exc)})
            if outcome.get("result") in {"logged", "merged"}:
                if outcome["result"] == "logged":
                    self.counters.records_logged += 1
                else:
                    self.counters.records_merged += 1
            else:
                self.counters.events_skipped += 1
            entry["events"].append(outcome)
        if meaningful:
            summary = ", ".join(f"{e['type']}:{e.get('result')}" + (f"({e.get('reason')})" if e.get("reason") else "") for e in entry["events"])
            log(f"heard: {text!r} ({result.get('elapsed_ms')} ms) {summary or 'no care event'}")
        with self._lock:
            if meaningful:
                self.history.appendleft(entry)
            if any(e.get("result") in {"logged", "merged"} for e in entry["events"]):
                self.last_action = entry
                for outcome in entry["events"]:
                    if outcome.get("record"):
                        self.recent_records.appendleft({"at": now, "result": outcome["result"], "record": outcome["record"]})
        return entry

    def apply_event(self, event: CareEvent, text: str, at: float, now: float) -> dict[str, Any]:
        if event.action == "sleep_start":
            return self._sleep_start(text, at)
        if event.action == "sleep_end":
            return self._sleep_end(text, at)
        window = self.settings.diaper_merge_s if event.type == "diaper" else self.settings.feeding_merge_s
        recent = self.client.list_care_records(event.type, since=at - window, limit=5)
        recent = [r for r in recent if at - window <= float(r.get("start_at") or 0.0) <= at + 60]
        # Only merge into a record of a compatible kind: breast milk then a formula top-up
        # are two feedings, not one "breast + formula" record.
        recent = [r for r in recent if compatible_kind(r, event.fields)]
        if recent:
            existing = recent[0]
            if existing.get("source") != "voice":
                return {"result": "skipped", "reason": "already_logged", "record": None, "record_id": existing.get("id")}
            updates = merge_fields(existing, event.fields)
            note = _append_note(existing.get("note"), text)
            if not updates and note == existing.get("note"):
                return {"result": "skipped", "reason": "duplicate", "record_id": existing.get("id")}
            payload = {key: value for key, value in existing.items() if key not in {"id", "created_at", "updated_at"}}
            payload.update(updates)
            payload["note"] = note
            record = self.client.update_care_record(str(existing["id"]), payload)
            return {"result": "merged", "record": record, "record_id": record.get("id")}
        payload = {
            "type": event.type,
            "start_at": round(at, 3),
            **event.fields,
            "note": _append_note(None, text),
            "source": "voice",
            "voice_text": text,
        }
        record = self.client.create_care_record(payload)
        return {"result": "logged", "record": record, "record_id": record.get("id")}

    def _open_sleep(self) -> dict[str, Any] | None:
        records = self.client.list_care_records("sleep", limit=3)
        return next((r for r in records if r.get("end_at") is None), None)

    def _sleep_start(self, text: str, at: float) -> dict[str, Any]:
        if self._open_sleep() is not None:
            return {"result": "skipped", "reason": "already_sleeping"}
        record = self.client.create_care_record(
            {"type": "sleep", "start_at": round(at, 3), "note": _append_note(None, text), "source": "voice", "voice_text": text}
        )
        return {"result": "logged", "record": record, "record_id": record.get("id")}

    def _sleep_end(self, text: str, at: float) -> dict[str, Any]:
        open_sleep = self._open_sleep()
        if open_sleep is None:
            return {"result": "skipped", "reason": "no_sleep_in_progress"}
        start = float(open_sleep.get("start_at") or 0.0)
        if at - start > MAX_SLEEP_S:
            return {"result": "skipped", "reason": "sleep_longer_than_24h", "record_id": open_sleep.get("id")}
        payload = {key: value for key, value in open_sleep.items() if key not in {"id", "created_at", "updated_at"}}
        payload["end_at"] = round(max(at, start + 60.0), 3)
        payload["note"] = _append_note(open_sleep.get("note"), text)
        record = self.client.update_care_record(str(open_sleep["id"]), payload)
        return {"result": "merged", "record": record, "record_id": record.get("id")}

    # ---------------------------------------------------------------- housekeeping

    def _housekeeping_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.apply_command()
                self.apply_pause()
                self._write_status()
            except Exception as exc:  # never let status reporting kill the worker
                log(f"housekeeping: {exc}")
            self._stop.wait(1.0)

    def apply_command(self) -> None:
        try:
            mtime = self.command_path.stat().st_mtime
        except FileNotFoundError:
            return
        if mtime == self._command_mtime:
            return
        self._command_mtime = mtime
        command = read_json(self.command_path)
        if "enabled" in command:
            enabled = bool(command["enabled"])
            if enabled != self.enabled:
                log(f"command: enabled={enabled}")
            self.enabled = enabled

    @property
    def paused(self) -> bool:
        return self.paused_until is not None and self.clock() < self.paused_until

    def apply_pause(self) -> None:
        """Pause lease from the main service: {"until": <epoch s>, "reason": "assistant"}.

        The lease expires by itself, so a crashed main service cannot leave ASR off.
        """
        lease = read_json(self.pause_path) if self.pause_path.exists() else {}
        try:
            until = float(lease.get("until")) if lease.get("until") is not None else None
        except (TypeError, ValueError):
            until = None
        was_paused = self.paused
        self.paused_until = until if until is not None and until > self.clock() else None
        self.pause_reason = str(lease.get("reason") or "") or None if self.paused_until else None
        if self.paused != was_paused:
            log(f"paused ({self.pause_reason})" if self.paused else "resumed")

    def _drop_queued_segments(self) -> None:
        while True:
            try:
                self._segments.get_nowait()
            except queue.Empty:
                return
            self.counters.dropped_segments += 1

    def _set_state(self, state: str, error: str | None = None) -> None:
        self.state = state
        if error is not None or state in {"listening", "disabled"}:
            self.error = error

    def status(self) -> dict[str, Any]:
        with self._lock:
            history = list(self.history)
            recent_records = list(self.recent_records)
            last_action = dict(self.last_action) if self.last_action else None
        counters = asdict(self.counters)
        audio_s = counters.pop("asr_audio_s_total")
        asr_ms = counters.pop("asr_ms_total")
        transcripts = max(1, self.counters.transcripts)
        return {
            "mode": "care_log",
            "running": not self._stop.is_set(),
            "pid": os.getpid(),
            "enabled": self.enabled,
            "paused": self.paused,
            "pause_reason": self.pause_reason,
            "paused_until": self.paused_until,
            "state": self.state,
            "error": self.error,
            "connected": self.connected,
            "vad_probability": round(self.segmenter.last_probability, 3) if self.segmenter else None,
            "provider": getattr(self.recognizer, "provider", None),
            "language": self.settings.language,
            "avg_asr_ms": round(asr_ms / transcripts, 1) if self.counters.transcripts else None,
            "avg_rtf": round(asr_ms / 1000.0 / audio_s, 3) if audio_s > 0 else None,
            "counters": counters,
            "last_transcript": history[0] if history else None,
            "history": history,
            "last_action": last_action,
            "recent_records": recent_records,
            "settings": self.settings.keyword_settings(),
            "started_at": self._started_at,
            "updated_at": self.clock(),
        }

    def _write_status(self) -> None:
        write_json(self.status_path, self.status())


# --------------------------------------------------------------------------- helpers


def _append_note(note: Any, text: str, limit: int = 200) -> str:
    """Notes carry what was heard: "语音：宝宝换尿布了 / 拉了好多便便"."""
    text = str(text or "").strip()
    note = str(note or "").strip()
    if not note:
        return f"语音：{text}"[:limit]
    if text and text not in note:
        note = f"{note} / {text}"
    return note[:limit]


def save_segment(segment: np.ndarray, keep: int = 30) -> str:
    from ai.audio_ai import write_waveform

    VOICE_SEGMENT_DIR.mkdir(parents=True, exist_ok=True)
    output = VOICE_SEGMENT_DIR / f"voice_{int(time.time() * 1000)}.wav"
    write_waveform(output, segment, SAMPLE_RATE)
    for old in sorted(VOICE_SEGMENT_DIR.glob("voice_*.wav"))[:-keep]:
        old.unlink(missing_ok=True)
    return str(output)


def log(message: str) -> None:
    print(f"[voice-care {time.strftime('%H:%M:%S')}] {message}", flush=True)


# --------------------------------------------------------------------------- main-service side


def read_voice_trigger_status(
    path: Path | None = None,
    now: float | None = None,
    command_path: Path | None = None,
) -> dict[str, Any]:
    """Used by main.py: worker status plus an ``online`` flag based on file age."""
    payload = read_json(path or VOICE_TRIGGER_STATUS_FILE)
    command = read_json(command_path) if command_path else read_json(VOICE_TRIGGER_COMMAND_FILE) if path is None else {}
    age = age_seconds(payload, now=now)
    online = age is not None and age <= STATUS_STALE_S and payload.get("running") is not False
    result = dict(payload)
    result.update({"online": online, "age_s": age})
    if not payload:
        result.update({"state": "offline", "error": "voice-trigger.service is not running"})
    elif not online:
        result.update({"state": "offline", "error": payload.get("error") or "voice-trigger worker status is stale"})
    if "enabled" in command:
        result["requested_enabled"] = bool(command["enabled"])
    return result


def write_voice_trigger_command(enabled: bool, path: Path | None = None) -> dict[str, Any]:
    payload = {"enabled": bool(enabled), "updated_at": time.time()}
    write_json(path or VOICE_TRIGGER_COMMAND_FILE, payload)
    return payload


# --------------------------------------------------------------------------- CLI


def run_test_wavs(settings: VoiceTriggerSettings, files: list[str], repeat: int = 0) -> int:
    from ai.asr import SenseVoiceRecognizer
    from ai.audio_ai import read_waveform

    model_dir = Path(settings.model_dir)
    model = model_dir / "model.int8.onnx"
    if not model.exists():
        model = model_dir / "model.onnx"
    started = time.perf_counter()
    recognizer = SenseVoiceRecognizer(
        model, model_dir / "tokens.txt", settings.language, settings.use_itn, settings.provider, settings.threads
    )
    print(f"model loaded in {time.perf_counter() - started:.2f}s provider={recognizer.provider}")
    matcher = CareIntentMatcher(settings)
    for file in files:
        waveform, rate = read_waveform(Path(file))
        if rate != SAMPLE_RATE:
            from pipelines.audio_pipeline import resample_mono

            waveform = resample_mono(waveform, rate, SAMPLE_RATE)
        result = recognizer.transcribe(waveform)
        for _ in range(max(0, repeat)):
            result = recognizer.transcribe(waveform)
        events = [asdict(event) for event in matcher.match(result["text"])]
        print(json.dumps({"file": file, **result, "events": events}, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    settings, args = load_settings(argv)
    if args.test_text:
        matcher = CareIntentMatcher(settings)
        for sentence in args.test_text:
            print(json.dumps({"text": sentence, "events": [asdict(e) for e in matcher.match(sentence)]}, ensure_ascii=False))
        return 0
    if args.test_wav:
        return run_test_wavs(settings, args.test_wav, args.benchmark)
    return VoiceTriggerWorker(settings).run()


if __name__ == "__main__":
    raise SystemExit(main())
