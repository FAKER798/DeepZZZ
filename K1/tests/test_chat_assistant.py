from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Iterator

from services.chat_assistant import (
    AssistantError,
    AssistantSettings,
    ChatAssistant,
    WorkloadPauser,
    build_context,
    load_assistant_settings,
    trim_history,
    validate_messages,
)

NOW = 1793899529.0

OVERVIEW = {
    "now": NOW,
    "profile": {"name": "豆包", "birth_date": "2026-10-16", "sex": "male"},
    "age": {"days": 21, "months": 0, "month_days": 21},
    "today": {
        "feeding": {"count": 4, "bottle_ml": 360, "breast_min": 15, "solid_count": 0},
        "diaper": {"count": 5, "wet": 4, "dirty": 2},
        "sleep": {"count": 3, "total_min": 250.0, "longest_min": 110.0},
    },
    "last_feeding": {"id": "f1", "type": "feeding", "start_at": NOW - 5400, "kind": "bottle", "milk": "formula", "amount_ml": 120},
    "last_diaper": None,
    "nap": {"state": "awake", "last_wake_at": NOW - 3000, "earliest_at": NOW + 600, "latest_at": NOW + 2400},
}
ROOM = {"has_reading": True, "age_s": 2.0, "temperature_c": 24.5, "humidity_percent": 51.0}


class FakeVision:
    def __init__(self) -> None:
        self.reasons: set[str] = set()
        self.history: list[str] = []

    def pause(self, reason: str) -> None:
        self.reasons.add(reason)
        self.history.append("pause")

    def resume(self, reason: str) -> None:
        self.reasons.discard(reason)
        self.history.append("resume")


class FakeOllama:
    def __init__(self, chunks: list[dict[str, Any]] | None = None, error: Exception | None = None) -> None:
        self.chunks = chunks if chunks is not None else [
            {"message": {"content": "吐奶很常见"}, "done": False},
            {"message": {"content": "，竖抱拍嗝即可。"}, "done": False},
            {"message": {"content": ""}, "done": True, "done_reason": "stop", "eval_count": 12, "eval_duration": 4_000_000_000, "prompt_eval_count": 210},
        ]
        self.error = error
        self.bodies: list[dict[str, Any]] = []
        self.closed = False
        self.on_chunk: Any = None

    def models(self, timeout: float) -> list[str]:
        return ["qwen3-1.7b-nothink:latest", "qwen3-0.6b-nothink:latest", "qwen3:0.6b"]

    def chat_stream(self, body: dict[str, Any], timeout: float) -> Iterator[dict[str, Any]]:
        self.bodies.append(body)
        try:
            if self.error:
                raise self.error
            for chunk in self.chunks:
                if self.on_chunk:
                    self.on_chunk()
                yield chunk
        finally:
            self.closed = True


def make_assistant(tmp: str, client: FakeOllama | None = None, **settings: Any):
    vision = FakeVision()
    pause_path = Path(tmp) / "voice_pause.json"
    pauser = WorkloadPauser(vision=vision, voice_pause_path=pause_path)
    client = client or FakeOllama()
    assistant = ChatAssistant(
        AssistantSettings(**settings),
        overview=lambda: OVERVIEW,
        room=lambda: ROOM,
        pauser=pauser,
        client=client,
    )
    return assistant, client, vision, pause_path


def events(lines: Iterator[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in lines]


class ValidationTests(unittest.TestCase):
    def test_last_message_must_be_user(self) -> None:
        with self.assertRaises(AssistantError):
            validate_messages({"messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]})
        with self.assertRaises(AssistantError):
            validate_messages({"messages": []})

    def test_long_question_says_longer_than(self) -> None:
        with self.assertRaises(AssistantError) as ctx:
            validate_messages({"messages": [{"role": "user", "content": "问" * 501}]})
        self.assertIn("longer than", str(ctx.exception))

    def test_trim_history_keeps_newest_and_starts_with_user(self) -> None:
        messages = [
            {"role": "user", "content": "a" * 900},
            {"role": "assistant", "content": "b" * 900},
            {"role": "user", "content": "c" * 600},
            {"role": "assistant", "content": "d" * 600},
            {"role": "user", "content": "最后的问题"},
        ]
        kept = trim_history(messages, 1600)
        self.assertEqual(kept[0]["role"], "user")
        self.assertEqual(kept[-1]["content"], "最后的问题")
        self.assertLessEqual(sum(len(m["content"]) for m in kept), 1600)
        self.assertEqual(len(kept), 3)

    def test_settings_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"assistant": {"model": "qwen3:1.7b", "max_tokens": "200", "pause_voice": False, "x": 1}}), encoding="utf-8")
            settings = load_assistant_settings(path)
        self.assertEqual(settings.model, "qwen3:1.7b")
        self.assertEqual(settings.max_tokens, 200)
        self.assertFalse(settings.pause_voice)


class ContextTests(unittest.TestCase):
    def test_context_and_facts(self) -> None:
        context, facts = build_context(OVERVIEW, ROOM)
        self.assertIn("豆包，男孩，出生 2026-10-16，现在 0 个月 21 天", context)
        self.assertIn("瓶喂共 360 ml", context)
        self.assertIn("瓶喂 配方奶 120 ml", context)
        self.assertIn("已清醒 50 分钟", context)
        self.assertIn("室温 24.5°C，湿度 51%", context)
        self.assertIn("不满 3 个月，只要发热", context)
        older = {**OVERVIEW, "age": {"days": 200, "months": 6, "month_days": 17}}
        self.assertNotIn("不满 3 个月", build_context(older, ROOM)[0])
        self.assertEqual(facts, ["豆包 0 个月 21 天", "今日喂养 4 次 · 睡眠 4 小时 10 分钟", "室温 24.5°C · 湿度 51%"])

    def test_events_go_into_context_and_facts(self) -> None:
        summary = {
            "window_s": 7200.0,
            "current": {"vision_state": "covered", "posture": "face_down", "active": ["face_cover_rollover"]},
            "types": {
                "cry": {"count": 2, "total_s": 188.0, "latest": {"start_at": NOW - 1800, "duration_s": 39.0, "ongoing": False}},
                "face_cover_rollover": {"count": 1, "total_s": 12.0, "latest": {"start_at": NOW - 12, "duration_s": 12.0, "ongoing": True}},
                "boundary": {"count": 0, "total_s": 0, "latest": None},
                "no_person": {"count": 0, "total_s": 0, "latest": None},
            },
        }
        context, facts = build_context(OVERVIEW, ROOM, summary)
        lines = context.splitlines()
        self.assertIn("摄像头当前画面：疑似遮脸或趴睡（鼻子和眼睛关键点都看不到），回答时先提醒家长立即查看宝宝。", lines)
        cry_clock = time.strftime("%H:%M", time.localtime(NOW - 1800))
        self.assertIn(f"哭闹 2 次（最近一次 {cry_clock} 开始，持续 39 秒）；遮脸/翻身 1 次（", context)
        self.assertIn("开始，仍在持续）。", context)
        # the event lines come after the time / nap lines and before the room line
        self.assertLess(lines.index(next(l for l in lines if l.startswith("宝宝已清醒"))), lines.index(next(l for l in lines if l.startswith("最近 2 小时"))))
        self.assertTrue(lines[-1].startswith("房间："))
        self.assertEqual(facts[2], "画面：疑似遮脸/翻身 · 近 2 小时哭闹 2 次 · 遮脸/翻身 1 次")

        side = {**summary, "current": {"vision_state": "covered", "posture": "side"}}
        self.assertIn("宝宝疑似翻身侧躺", build_context(OVERVIEW, ROOM, side)[0])

    def test_no_events_unknown_state_and_old_call(self) -> None:
        empty = {"window_s": 600, "current": {"vision_state": "unknown"},
                 "types": {t: {"count": 0, "total_s": 0, "latest": None} for t in ("cry", "face_cover_rollover", "boundary", "no_person")}}
        context, facts = build_context(OVERVIEW, ROOM, empty)
        self.assertNotIn("摄像头当前画面", context)
        self.assertIn("最近 10 分钟摄像头和麦克风的自动监测（可能有误报）：没有哭闹、遮脸/翻身、越界或离开画面的记录。", context)
        self.assertEqual(facts[2], "近 10 分钟哭闹 0 次 · 遮脸/翻身 0 次")
        self.assertEqual(build_context(OVERVIEW, ROOM), build_context(OVERVIEW, ROOM, None))

    def test_stale_sensor_and_missing_profile(self) -> None:
        context, facts = build_context({"now": NOW, "profile": {}, "age": None, "today": {}}, {"has_reading": True, "age_s": 9999, "temperature_c": 30})
        self.assertNotIn("室温", context)
        self.assertEqual(facts, ["今日喂养 0 次 · 睡眠 0 分钟"])


class StreamTests(unittest.TestCase):
    def test_reply_streams_and_pauses_workloads_only_while_generating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assistant, client, vision, pause_path = make_assistant(tmp)
            seen: list[tuple[bool, bool]] = []
            client.on_chunk = lambda: seen.append(("assistant" in vision.reasons, pause_path.exists()))

            result = events(assistant.stream([{"role": "user", "content": "宝宝吐奶怎么办？"}]))

            self.assertEqual([e["type"] for e in result], ["start", "delta", "delta", "done"])
            self.assertEqual(result[0]["facts"][0], "豆包 0 个月 21 天")
            self.assertEqual(result[-1]["reply"], "吐奶很常见，竖抱拍嗝即可。")
            self.assertFalse(result[-1]["truncated"])
            self.assertEqual(result[-1]["tokens"], 12)
            self.assertEqual(result[-1]["tokens_per_s"], 3.0)
            self.assertTrue(all(flags == (True, True) for flags in seen))
            self.assertEqual(vision.history, ["pause", "resume"])
            self.assertFalse(pause_path.exists())
            body = client.bodies[0]
            self.assertEqual(body["model"], "qwen3-1.7b-nothink")
            self.assertEqual(body["messages"][0]["role"], "system")
            self.assertIn("豆包", body["messages"][0]["content"])
            self.assertEqual(body["options"]["num_predict"], 160)
            self.assertEqual(body["options"]["num_ctx"], 2048)
            self.assertFalse(assistant.status()["busy"])

    def test_stream_reads_event_summary_and_survives_its_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assistant, client, vision, _ = make_assistant(tmp)
            asked: list[tuple[float, bool]] = []

            def summary(window_s: float) -> dict[str, Any]:
                asked.append((window_s, "assistant" in vision.reasons))
                return {"window_s": window_s, "current": {"vision_state": "visible"},
                        "types": {"cry": {"count": 1, "latest": None}}}

            assistant.events = summary
            result = events(assistant.stream([{"role": "user", "content": "宝宝刚才睡得怎么样？"}]))
            self.assertEqual(asked, [(7200.0, False)])  # read before YOLO is paused
            self.assertIn("画面：面部可见 · 近 2 小时哭闹 1 次 · 遮脸/翻身 0 次", result[0]["facts"])
            self.assertIn("检测到宝宝，面部可见", client.bodies[0]["messages"][0]["content"])

            def broken(window_s: float) -> dict[str, Any]:
                raise OSError("disk")

            assistant.events = broken
            result = events(assistant.stream([{"role": "user", "content": "q"}]))
            self.assertEqual(result[-1]["type"], "done")
            self.assertFalse(any(f.startswith("画面") or "哭闹" in f for f in result[0]["facts"]))

    def test_only_the_new_question_is_sent_by_default(self) -> None:
        conversation = [
            {"role": "user", "content": "宝宝现在该小睡了吗？"},
            {"role": "assistant", "content": "结论：宝宝现在应该已经入睡了。"},
            {"role": "user", "content": "宝宝现在该喂奶了吗"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            assistant, client, *_ = make_assistant(tmp)
            events(assistant.stream(conversation))
            self.assertEqual([m["role"] for m in client.bodies[0]["messages"]], ["system", "user"])
            self.assertEqual(client.bodies[0]["messages"][-1]["content"], "宝宝现在该喂奶了吗")
            with_history, client2, *_ = make_assistant(tmp, history_turns=1)
            events(with_history.stream(conversation))
            self.assertEqual([m["role"] for m in client2.bodies[0]["messages"]], ["system", "user", "assistant", "user"])

    def test_truncated_when_length_limit_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeOllama([{"message": {"content": "很长"}, "done": False}, {"done": True, "done_reason": "length", "eval_count": 2, "eval_duration": 1e9}])
            assistant, *_ = make_assistant(tmp, client)
            result = events(assistant.stream([{"role": "user", "content": "q"}]))
        self.assertTrue(result[-1]["truncated"])

    def test_second_question_while_busy_gets_one_busy_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assistant, *_ = make_assistant(tmp)
            first = assistant.stream([{"role": "user", "content": "q1"}])
            next(first)  # holds the lock
            busy = events(assistant.stream([{"role": "user", "content": "q2"}]))
            self.assertEqual(busy, [{"type": "error", "code": "busy", "error": "上一个问题还在回答中，请稍后再问"}])
            self.assertTrue(assistant.status()["busy"])
            list(first)
            self.assertFalse(assistant.status()["busy"])

    def test_disconnect_stops_upstream_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assistant, client, vision, pause_path = make_assistant(tmp)
            stream = assistant.stream([{"role": "user", "content": "q"}])
            self.assertEqual(json.loads(next(stream))["type"], "start")
            self.assertEqual(json.loads(next(stream))["type"], "delta")
            self.assertTrue(pause_path.exists())
            stream.close()  # phone pressed stop
            self.assertTrue(client.closed)
            self.assertEqual(vision.reasons, set())
            self.assertFalse(pause_path.exists())
            self.assertFalse(assistant.status()["busy"])

    def test_llm_failure_is_an_error_event_and_resumes(self) -> None:
        from urllib.error import URLError

        with tempfile.TemporaryDirectory() as tmp:
            assistant, client, vision, pause_path = make_assistant(tmp, FakeOllama(error=URLError("connection refused")))
            result = events(assistant.stream([{"role": "user", "content": "q"}]))
        self.assertEqual(result[-1]["type"], "error")
        self.assertEqual(result[-1]["code"], "llm")
        self.assertIn("本地模型没有响应", result[-1]["error"])
        self.assertEqual(vision.history, ["pause", "resume"])

    def test_non_streaming_reply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assistant, *_ = make_assistant(tmp)
            reply = assistant.reply([{"role": "user", "content": "q"}])
        self.assertEqual(reply["reply"], "吐奶很常见，竖抱拍嗝即可。")
        self.assertIn("facts", reply)

    def test_pauses_can_be_turned_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assistant, client, vision, pause_path = make_assistant(tmp, pause_vision=False, pause_voice=False)
            client.on_chunk = lambda: self.assertFalse(pause_path.exists())
            events(assistant.stream([{"role": "user", "content": "q"}]))
        self.assertEqual(vision.history, [])

    def test_status_reports_model_presence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assistant, *_ = make_assistant(tmp)
            self.assertEqual(assistant.status()["llm"], {"ok": True, "status": "ok"})
            missing, *_ = make_assistant(tmp, model="qwen3:4b")
            self.assertFalse(missing.status()["llm"]["ok"])


class PauseLeaseTests(unittest.TestCase):
    def test_lease_is_refreshed_and_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "voice_pause.json"
            clock = [100.0]
            pauser = WorkloadPauser(voice_pause_path=path, lease_s=45.0, refresh_s=0.01, clock=lambda: clock[0])
            with pauser.paused("assistant", vision=False):
                first = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(first["until"], 145.0)
                clock[0] = 200.0
                done = threading.Event()
                for _ in range(200):
                    if json.loads(path.read_text(encoding="utf-8"))["until"] == 245.0:
                        done.set()
                        break
                    threading.Event().wait(0.01)
                self.assertTrue(done.is_set())
            self.assertFalse(path.exists())


class VoiceWorkerPauseTests(unittest.TestCase):
    def test_worker_honours_and_expires_pause_lease(self) -> None:
        from services.voice_trigger import VoiceTriggerSettings, VoiceTriggerWorker

        with tempfile.TemporaryDirectory() as tmp:
            clock = [1000.0]
            settings = VoiceTriggerSettings(
                fuzzy_pinyin=False,
                status_path=str(Path(tmp) / "status.json"),
                command_path=str(Path(tmp) / "command.json"),
                pause_path=str(Path(tmp) / "pause.json"),
            )
            worker = VoiceTriggerWorker(settings, client=object(), recognizer=object(), vad=lambda _: 0.0, clock=lambda: clock[0])
            worker.apply_pause()
            self.assertFalse(worker.paused)
            Path(settings.pause_path).write_text(json.dumps({"until": 1045.0, "reason": "assistant"}), encoding="utf-8")
            worker.apply_pause()
            self.assertTrue(worker.paused)
            self.assertEqual(worker.status()["pause_reason"], "assistant")
            clock[0] = 1046.0  # lease expired although the file is still there
            self.assertFalse(worker.paused)
            worker.apply_pause()
            self.assertIsNone(worker.pause_reason)


if __name__ == "__main__":
    unittest.main()
