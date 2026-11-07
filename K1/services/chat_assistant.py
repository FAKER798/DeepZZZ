"""AI Q&A for the naviga app (/api/assistant/status, /api/assistant/chat).

The phone sends the conversation, the board answers with the local Ollama model
(qwen3-0.6b-nothink by default) and streams NDJSON events back:

    {"type": "start", "facts": [...]}
    {"type": "delta", "text": "..."}            (repeated)
    {"type": "done", "reply": "...", ...}       or {"type": "error", "code": ..., "error": "<中文>"}

The context also carries the vision / cry episodes of the last 2 hours and the current
camera state (services/event_log.py), shown in the app as a "画面：… · 近 2 小时哭闹 N 次" tag.

While a reply is being generated the pose model (YOLO, CPU0-3) and the voice
care-log worker (ASR, CPU0-3) are paused so the LLM gets those cores; both are
resumed as soon as the reply ends, fails or the phone disconnects.
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib import error as urlerror
from urllib import request as urlrequest

from k1edge.runtime_store import write_json
from utils.paths import PROJECT_ROOT, VOICE_PAUSE_FILE

MAX_QUESTION_CHARS = 500
SENSOR_MAX_AGE_S = 600.0

SYSTEM_PROMPT = (
    "你是 0-3 岁宝宝的育儿助手，回答家长的问题。"
    "用简体中文，最多 3 句话：第一句直接回答，后面给 1-2 条最重要的做法。"
    "不要写“结论”“做法”这类标签，不用列表、标题和 Markdown。"
    "不做诊断，不给药物剂量。遇到发热、呼吸困难、持续呕吐、精神差、抽搐等情况，"
    "提醒尽快就医或拨打 120。"
)


class AssistantError(Exception):
    """Rejected before streaming starts; maps to a plain JSON error."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class AssistantSettings:
    enabled: bool = True
    ollama_url: str = "http://127.0.0.1:11434"
    # 1.7b answers much better than 0.6b at ~2.8 tok/s; it is a variant of qwen3:1.7b with
    # the current Qwen3 template (system prompt once, empty think block) - see scripts/create_assistant_model.sh.
    model: str = "qwen3-1.7b-nothink"
    max_tokens: int = 160  # ~3 short sentences; at 2.8 tok/s the cap is ~1 minute
    temperature: float = 0.6
    num_thread: int = 4  # CPU0-3 are free while YOLO and ASR are paused
    keep_alive: str = "30m"  # idle model costs RAM only; avoids an ~11 s reload per question
    num_ctx: int = 2048  # prompt is ~300 tokens; Ollama's default 8192 needs 900 MB of KV cache
    # Earlier Q&A turns sent along with the question. 0 = only the new question: the 0.6b
    # model copies its previous answer verbatim when it sees it, and each turn adds prefill time.
    history_turns: int = 0
    history_chars: int = 1600
    pause_vision: bool = True
    pause_voice: bool = True
    event_window_s: float = 7200.0  # vision / cry episodes from the last 2 h go into the context
    request_timeout_s: float = 140.0  # app gives up after 150 s without a line
    health_timeout_s: float = 1.0
    health_cache_s: float = 5.0


def load_assistant_settings(config_path: Path | None = None) -> AssistantSettings:
    settings = AssistantSettings()
    try:
        values = json.loads((config_path or PROJECT_ROOT / "config.json").read_text(encoding="utf-8")).get("assistant") or {}
    except (FileNotFoundError, json.JSONDecodeError, AttributeError, OSError):
        values = {}
    if not isinstance(values, dict):
        return settings
    known = {f.name: f.type for f in fields(AssistantSettings)}
    updates: dict[str, Any] = {}
    for key, value in values.items():
        if key not in known:
            continue
        default = getattr(settings, key)
        try:
            updates[key] = type(default)(value) if not isinstance(default, bool) else bool(value)
        except (TypeError, ValueError):
            continue
    return replace(settings, **updates)


# --------------------------------------------------------------------------- Ollama


class OllamaClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def models(self, timeout: float) -> list[str]:
        with urlrequest.urlopen(f"{self.base_url}/api/tags", timeout=timeout) as response:
            payload = json.load(response)
        return [str(item.get("name") or "") for item in payload.get("models") or []]

    def chat_stream(self, body: dict[str, Any], timeout: float) -> Iterator[dict[str, Any]]:
        """Yield Ollama's streamed chunks. Closing the generator closes the socket,
        which makes Ollama stop generating."""
        request = urlrequest.Request(
            f"{self.base_url}/api/chat",
            json.dumps({**body, "stream": True}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        with urlrequest.urlopen(request, timeout=timeout) as response:
            for raw in response:
                line = raw.strip()
                if line:
                    yield json.loads(line)


# --------------------------------------------------------------------------- pausing


class WorkloadPauser:
    """Pause YOLO (in-process) and the voice worker (pause-lease file) around a reply."""

    def __init__(
        self,
        vision: Any = None,
        voice_pause_path: Path | None = VOICE_PAUSE_FILE,
        lease_s: float = 45.0,
        refresh_s: float = 15.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.vision = vision
        self.voice_pause_path = voice_pause_path
        self.lease_s = lease_s
        self.refresh_s = refresh_s
        self.clock = clock

    def _write_lease(self, reason: str) -> None:
        if self.voice_pause_path is not None:
            now = self.clock()
            write_json(self.voice_pause_path, {"until": now + self.lease_s, "reason": reason, "updated_at": now})

    @contextmanager
    def paused(self, reason: str = "assistant", vision: bool = True, voice: bool = True) -> Iterator[None]:
        stop = threading.Event()
        refresher: threading.Thread | None = None
        if vision and self.vision is not None:
            self.vision.pause(reason)
        try:
            if voice and self.voice_pause_path is not None:
                self._write_lease(reason)

                def refresh() -> None:
                    while not stop.wait(self.refresh_s):
                        try:
                            self._write_lease(reason)
                        except OSError:
                            pass  # retried on the next tick; the old lease is still valid

                refresher = threading.Thread(target=refresh, name="voice-pause-lease", daemon=True)
                refresher.start()
            yield
        finally:
            stop.set()
            if refresher is not None:
                refresher.join(timeout=1.0)
            if voice and self.voice_pause_path is not None:
                try:
                    self.voice_pause_path.unlink()
                except FileNotFoundError:
                    pass
            if vision and self.vision is not None:
                self.vision.resume(reason)


# --------------------------------------------------------------------------- assistant


def event_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def trim_history(messages: list[dict[str, str]], max_chars: int) -> list[dict[str, str]]:
    """Keep the newest turns within max_chars (the last question always stays); start with a user turn."""
    kept: list[dict[str, str]] = []
    total = 0
    for message in reversed(messages):
        size = len(message["content"])
        if kept and total + size > max_chars:
            break
        kept.append(message)
        total += size
    kept.reverse()
    while kept and kept[0]["role"] != "user":
        kept.pop(0)
    return kept


def validate_messages(body: dict[str, Any]) -> list[dict[str, str]]:
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise AssistantError("messages must be a non-empty list")
    cleaned: list[dict[str, str]] = []
    for item in messages:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            raise AssistantError("each message needs role user or assistant")
        content = item.get("content")
        if not isinstance(content, str):
            raise AssistantError("message content must be a string")
        cleaned.append({"role": item["role"], "content": content})
    last = cleaned[-1]
    if last["role"] != "user" or not last["content"].strip():
        raise AssistantError("the last message must be a non-empty user question")
    if len(last["content"]) > MAX_QUESTION_CHARS:
        raise AssistantError(f"question is longer than {MAX_QUESTION_CHARS} characters")
    return cleaned


def _clock_text(ts: Any) -> str | None:
    try:
        return time.strftime("%H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError):
        return None


def _minutes_text(minutes: float) -> str:
    minutes = max(0, int(round(minutes)))
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours} 小时 {mins} 分钟"
    return f"{hours} 小时" if hours else f"{mins} 分钟"


FEEDING_KIND = {"breast": "亲喂", "bottle": "瓶喂", "solid": "辅食"}
MILK_TEXT = {"breast_milk": "母乳", "formula": "配方奶"}


def feeding_text(record: dict[str, Any]) -> str:
    parts = [FEEDING_KIND.get(str(record.get("kind")), "喂养")]
    if record.get("milk") in MILK_TEXT:
        parts.append(MILK_TEXT[record["milk"]])
    if record.get("amount_ml"):
        parts.append(f"{record['amount_ml']} ml")
    if record.get("duration_min"):
        parts.append(f"{record['duration_min']} 分钟")
    if record.get("food"):
        parts.append(str(record["food"]))
    return " ".join(parts)


EVENT_ORDER = ("cry", "face_cover_rollover", "boundary", "no_person")
EVENT_TEXT = {"cry": "哭闹", "face_cover_rollover": "遮脸/翻身", "boundary": "越出安全区域", "no_person": "离开画面"}
VISION_LINE = {
    "visible": "摄像头当前画面：检测到宝宝，面部可见。",
    "covered": "摄像头当前画面：疑似遮脸或趴睡（鼻子和眼睛关键点都看不到），回答时先提醒家长立即查看宝宝。",
    "no_person": "摄像头当前画面：没有检测到宝宝。",
}
VISION_FACT = {"visible": "面部可见", "covered": "疑似遮脸/翻身", "no_person": "画面无人"}


def _duration_text(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    return f"{int(round(seconds))} 秒" if seconds < 60 else _minutes_text(seconds / 60)


def _window_text(window_s: float) -> str:
    window_s = float(window_s)
    if window_s >= 3600 and window_s % 3600 == 0:
        return f"{int(window_s // 3600)} 小时"
    return _minutes_text(window_s / 60)


def event_context(events: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    """Prompt lines and one app fact tag from EventLog.summary()."""
    if not isinstance(events, dict) or not isinstance(events.get("types"), dict):
        return [], []
    types = events["types"]
    window = _window_text(events.get("window_s") or 7200)
    lines: list[str] = []
    state = str((events.get("current") or {}).get("vision_state") or "")
    if state in VISION_LINE:
        lines.append(VISION_LINE[state])
    items = []
    for name in EVENT_ORDER:
        info = types.get(name) or {}
        count = int(info.get("count") or 0)
        if not count:
            continue
        latest = info.get("latest") or {}
        detail = ""
        start = _clock_text(latest.get("start_at"))
        if start:
            if latest.get("ongoing"):
                detail = f"（最近一次 {start} 开始，仍在持续）"
            else:
                detail = f"（最近一次 {start} 开始，持续 {_duration_text(latest.get('duration_s') or 0)}）"
        items.append(f"{EVENT_TEXT[name]} {count} 次{detail}")
    summary = "；".join(items) if items else "没有哭闹、遮脸/翻身、越界或离开画面的记录"
    lines.append(f"最近 {window}摄像头和麦克风的自动监测（可能有误报）：{summary}。")
    tag = []
    if state in VISION_FACT:
        tag.append(f"画面：{VISION_FACT[state]}")
    count = lambda name: int((types.get(name) or {}).get("count") or 0)  # noqa: E731
    tag.append(f"近 {window}哭闹 {count('cry')} 次")
    tag.append(f"遮脸/翻身 {count('face_cover_rollover')} 次")
    if count("boundary"):
        tag.append(f"越界 {count('boundary')} 次")
    return lines, [" · ".join(tag)]


def build_context(
    overview: dict[str, Any] | None,
    room: dict[str, Any] | None,
    events: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    """Device records for the prompt, plus the short "facts" tags shown in the app."""
    lines: list[str] = []
    facts: list[str] = []
    overview = overview or {}
    now = float(overview.get("now") or time.time())

    profile = overview.get("profile") or {}
    age = overview.get("age") or None
    name = str(profile.get("name") or "宝宝")
    sex = {"female": "女孩", "male": "男孩"}.get(str(profile.get("sex")), "")
    if age:
        age_text = f"{age.get('months', 0)} 个月 {age.get('month_days', 0)} 天"
        born = f"，出生 {profile['birth_date']}" if profile.get("birth_date") else ""
        lines.append(f"{name}{('，' + sex) if sex else ''}{born}，现在 {age_text}。")
        facts.append(f"{name} {age_text}")
        if int(age.get("days") or 0) < 90:
            lines.append("注意：宝宝不满 3 个月，只要发热（体温 38°C 及以上）就要立即就医，不要在家自行降温或喂水。")
    elif profile.get("name"):
        lines.append(f"宝宝小名{name}，没有填写生日。")

    today = overview.get("today") or {}
    feeding = today.get("feeding") or {}
    diaper = today.get("diaper") or {}
    sleep = today.get("sleep") or {}
    summary = [f"喂养 {feeding.get('count', 0)} 次"]
    if feeding.get("bottle_ml"):
        summary.append(f"瓶喂共 {feeding['bottle_ml']} ml")
    if feeding.get("breast_min"):
        summary.append(f"亲喂共 {feeding['breast_min']} 分钟")
    summary.append(f"尿布 {diaper.get('count', 0)} 次（湿 {diaper.get('wet', 0)}、便 {diaper.get('dirty', 0)}）")
    sleep_min = float(sleep.get("total_min") or 0.0)
    summary.append(f"睡眠 {_minutes_text(sleep_min)}")
    lines.append("今天的记录：" + "，".join(summary) + "。")
    facts.append(f"今日喂养 {feeding.get('count', 0)} 次 · 睡眠 {_minutes_text(sleep_min)}")

    last_feeding = overview.get("last_feeding")
    if isinstance(last_feeding, dict) and _clock_text(last_feeding.get("start_at")):
        lines.append(f"上次喂养在 {_clock_text(last_feeding['start_at'])}（{feeding_text(last_feeding)}）。")
    last_diaper = overview.get("last_diaper")
    if isinstance(last_diaper, dict) and _clock_text(last_diaper.get("start_at")):
        lines.append(f"上次换尿布在 {_clock_text(last_diaper['start_at'])}。")

    # Lines below change minute by minute; keep them last so the prefix above stays cacheable.
    lines.append(f"现在时间 {_clock_text(now)}。")
    nap = overview.get("nap") or {}
    state = nap.get("state")
    if state == "sleeping" and nap.get("started_at"):
        lines.append(f"宝宝正在睡，{_clock_text(nap['started_at'])} 入睡，已睡 {_minutes_text((now - float(nap['started_at'])) / 60)}。")
    elif state == "awake" and nap.get("last_wake_at"):
        awake = _minutes_text((now - float(nap["last_wake_at"])) / 60)
        window = ""
        if nap.get("earliest_at") and nap.get("latest_at"):
            window = f"，按记录预测建议在 {_clock_text(nap['earliest_at'])}-{_clock_text(nap['latest_at'])} 之间小睡"
        lines.append(f"宝宝已清醒 {awake}{window}。")

    event_lines, event_facts = event_context(events)
    lines.extend(event_lines)
    facts.extend(event_facts)

    room = room or {}
    temp, humidity = room.get("temperature_c"), room.get("humidity_percent")
    age_s = room.get("age_s")
    fresh = room.get("has_reading") and (age_s is None or float(age_s) <= SENSOR_MAX_AGE_S)
    if fresh and temp is not None:
        room_text = f"室温 {float(temp):.1f}°C" + (f"，湿度 {float(humidity):.0f}%" if humidity is not None else "")
        lines.append(f"房间：{room_text}。")
        facts.append(room_text.replace("，", " · "))
    return "\n".join(lines), facts


class ChatAssistant:
    def __init__(
        self,
        settings: AssistantSettings | None = None,
        overview: Callable[[], dict[str, Any]] | None = None,
        room: Callable[[], dict[str, Any]] | None = None,
        pauser: WorkloadPauser | None = None,
        events: Callable[[], dict[str, Any]] | None = None,
        client: OllamaClient | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings or AssistantSettings()
        self.overview = overview or (lambda: {})
        self.room = room or (lambda: {})
        self.events = events or (lambda: {})
        self.pauser = pauser or WorkloadPauser(voice_pause_path=None)
        self.client = client or OllamaClient(self.settings.ollama_url)
        self.clock = clock
        self._busy = threading.Lock()
        self._health: tuple[float, dict[str, Any]] | None = None
        self.last: dict[str, Any] | None = None

    # ---------------------------------------------------------------- status

    def health(self) -> dict[str, Any]:
        now = self.clock()
        if self._health and now - self._health[0] < self.settings.health_cache_s:
            return self._health[1]
        try:
            models = self.client.models(timeout=self.settings.health_timeout_s)
            wanted = self.settings.model
            present = any(name == wanted or name == f"{wanted}:latest" for name in models)
            result = {"ok": present, "status": "ok" if present else f"model {wanted} is not installed"}
        except Exception as exc:  # Ollama down or slow
            result = {"ok": False, "status": f"ollama unreachable: {exc}"}
        self._health = (now, result)
        return result

    def status(self) -> dict[str, Any]:
        return {
            "available": self.settings.enabled,
            "busy": self._busy.locked(),
            "llm": self.health(),
            "model": self.settings.model,
            "max_tokens": self.settings.max_tokens,
            "pauses": {"vision": self.settings.pause_vision, "voice": self.settings.pause_voice},
            "last": self.last,
        }

    # ---------------------------------------------------------------- chat

    def prepare(self, body: dict[str, Any]) -> list[dict[str, str]]:
        """Validate before streaming; raises AssistantError for plain JSON errors."""
        if not self.settings.enabled:
            raise AssistantError("assistant is not configured on this device", 503)
        return validate_messages(body)

    def _prompt(self, messages: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[str]]:
        try:
            overview = self.overview()
        except Exception:
            overview = {}
        try:
            room = self.room()
        except Exception:
            room = {}
        try:
            events = self.events()
        except Exception:
            events = {}
        context, facts = build_context(overview, room, events)
        system = f"{SYSTEM_PROMPT}\n\n下面是看护设备记录的情况，回答时可以参考：\n{context}"
        history = messages[-(2 * max(0, self.settings.history_turns) + 1):]
        return [{"role": "system", "content": system}, *trim_history(history, self.settings.history_chars)], facts

    def stream(self, messages: list[dict[str, str]]) -> Iterator[str]:
        """NDJSON lines for one reply. Busy -> a single error line (HTTP 200, as the app expects)."""
        if not self._busy.acquire(blocking=False):
            yield event_line({"type": "error", "code": "busy", "error": "上一个问题还在回答中，请稍后再问"})
            return
        upstream: Iterator[dict[str, Any]] | None = None
        started = self.clock()
        try:
            prompt, facts = self._prompt(messages)
            yield event_line({"type": "start", "facts": facts})
            settings = self.settings
            body = {
                "model": settings.model,
                "messages": prompt,
                "keep_alive": settings.keep_alive,
                "options": {
                    "num_predict": settings.max_tokens,
                    "temperature": settings.temperature,
                    "num_thread": settings.num_thread,
                    "num_ctx": settings.num_ctx,
                },
            }
            parts: list[str] = []
            final: dict[str, Any] = {}
            first_token_at: float | None = None
            with self.pauser.paused("assistant", vision=settings.pause_vision, voice=settings.pause_voice):
                upstream = self.client.chat_stream(body, timeout=settings.request_timeout_s)
                for chunk in upstream:
                    if chunk.get("error"):
                        raise RuntimeError(str(chunk["error"]))
                    text = str((chunk.get("message") or {}).get("content") or "")
                    if text:
                        if first_token_at is None:
                            first_token_at = self.clock()
                            text = text.lstrip()
                            if not text:
                                continue
                        parts.append(text)
                        yield event_line({"type": "delta", "text": text})
                    if chunk.get("done"):
                        final = chunk
                        break
            # YOLO and ASR are running again from here on.
            reply = "".join(parts).strip()
            stats = self._stats(started, first_token_at, final)
            self.last = stats
            if not reply:
                yield event_line({"type": "error", "code": "llm", "error": "本地模型没有给出回答，请换个问法再试"})
                return
            yield event_line(
                {"type": "done", "reply": reply, "truncated": final.get("done_reason") == "length", **stats}
            )
        except GeneratorExit:  # phone stopped / disconnected
            raise
        except urlerror.URLError as exc:
            yield event_line({"type": "error", "code": "llm", "error": f"本地模型没有响应：{getattr(exc, 'reason', exc)}"})
        except TimeoutError:
            yield event_line({"type": "error", "code": "llm", "error": "本地模型响应超时，请稍后再试"})
        except Exception as exc:
            yield event_line({"type": "error", "code": "llm", "error": f"本地模型出错：{exc}"})
        finally:
            if upstream is not None:
                upstream.close()  # stops generation in Ollama if we are cut short
            self._busy.release()

    def _stats(self, started: float, first_token_at: float | None, final: dict[str, Any]) -> dict[str, Any]:
        now = self.clock()
        tokens = int(final.get("eval_count") or 0)
        eval_s = float(final.get("eval_duration") or 0) / 1e9
        return {
            "elapsed_ms": round((now - started) * 1000.0, 1),
            "first_token_ms": round((first_token_at - started) * 1000.0, 1) if first_token_at else None,
            "tokens": tokens,
            "tokens_per_s": round(tokens / eval_s, 2) if eval_s > 0 else None,
            "prompt_tokens": final.get("prompt_eval_count"),
        }

    def reply(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """Non-streaming mode (stream: false) for scripts and the web page."""
        events = [json.loads(line) for line in self.stream(messages)]
        last = events[-1] if events else {"type": "error", "code": "llm", "error": "no reply"}
        if last.get("type") == "error":
            raise AssistantError(str(last.get("error")), 409 if last.get("code") == "busy" else 502)
        start = next((event for event in events if event.get("type") == "start"), {})
        return {**{key: value for key, value in last.items() if key != "type"}, "facts": start.get("facts", [])}
