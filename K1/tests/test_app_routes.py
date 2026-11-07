from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import wave

import numpy as np

from k1edge.app import create_dataset_app, create_main_app
from k1edge.config import parse_config
from k1edge.runtime import AppRuntime


def make_runtime() -> AppRuntime:
    return AppRuntime(parse_config(["--no-camera", "--disable-yamnet", "--enable-dataset-capture"]))


class AppRouteTests(unittest.TestCase):
    def test_healthz_and_status_shapes(self) -> None:
        runtime = make_runtime()
        client = create_main_app(runtime).test_client()

        health = client.get("/api/healthz")
        self.assertEqual(health.status_code, 200)
        self.assertIs(health.get_json()["ok"], True)
        self.assertIn("runtime", health.get_json())

        status = client.get("/api/status")
        payload = status.get_json()
        self.assertEqual(status.status_code, 200)
        self.assertIs(payload["ok"], True)
        self.assertIn("camera", payload)
        self.assertIn("light", payload)
        self.assertIn("temperature_humidity", payload)

    def test_snapshot_returns_compatible_error_shape_when_empty(self) -> None:
        runtime = make_runtime()
        client = create_main_app(runtime).test_client()

        response = client.get("/api/snapshot")

        self.assertEqual(response.status_code, 404)
        self.assertIs(response.get_json()["ok"], False)

    def test_light_post_writes_dual_pwm_command(self) -> None:
        runtime = make_runtime()
        with tempfile.TemporaryDirectory() as tmp:
            runtime.hardware.light.command_path = Path(tmp) / "pwm_dual_command.json"
            runtime.hardware.light.status_path = Path(tmp) / "pwm_dual_status.json"
            runtime.hardware.light.ack_timeout_s = 0.05
            client = create_main_app(runtime).test_client()

            response = client.post("/api/light", json={"duty": 42, "frequency_hz": 20000})
            payload = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertIs(payload["ok"], True)
            self.assertEqual(payload["light"]["target_duty"], 42.0)
            command = json.loads(runtime.hardware.light.command_path.read_text(encoding="utf-8"))
            self.assertEqual((command["duty"], command["enabled"], command["frequency_hz"]), (42.0, True, 20000))

            bad = client.post("/api/light", json={"duty": 10, "frequency_hz": 5})
            self.assertEqual(bad.status_code, 400)

    def test_dataset_stats_route(self) -> None:
        runtime = make_runtime()
        client = create_dataset_app(runtime).test_client()

        response = client.get("/api/dataset/stats")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIs(payload["ok"], True)
        self.assertIs(payload["result"]["enabled"], True)

    def test_audio_playback_route_records_when_no_buffer(self) -> None:
        runtime = make_runtime()
        client = create_main_app(runtime).test_client()

        with tempfile.TemporaryDirectory() as tmp:
            runtime.audio.pipeline.args.audio_device = "input"
            with patch("pipelines.audio_pipeline.AUDIO_DIR", Path(tmp)):
                with patch("pipelines.audio_pipeline.record_waveform") as record_waveform:
                    with patch("pipelines.audio_pipeline.subprocess.run") as run:
                        record_waveform.return_value = (np.zeros(16000, dtype=np.float32), 16000)
                        run.return_value.returncode = 0
                        run.return_value.stdout = ""
                        run.return_value.stderr = ""

                        response = client.post(
                            "/api/audio/playback",
                            json={"seconds": 1, "volume": 1.2, "output_device": "default"},
                        )
                        thread = runtime.audio.pipeline._playback_thread
                        if thread is not None:
                            thread.join(timeout=2)
                        payload = response.get_json()
                        with wave.open(payload["result"]["file"], "rb") as handle:
                            self.assertEqual(handle.getframerate(), 48000)
                            self.assertEqual(handle.getnchannels(), 2)

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertIs(payload["ok"], True)
        self.assertEqual(payload["result"]["output_device"], "plughw:CARD=UACDemoV10,DEV=0")
        self.assertEqual(payload["result"]["volume"], 1.2)
        record_waveform.assert_called_once()

    def test_audio_playback_capture_start_stop_and_play(self) -> None:
        runtime = make_runtime()
        client = create_main_app(runtime).test_client()

        with tempfile.TemporaryDirectory() as tmp:
            with patch("pipelines.audio_pipeline.AUDIO_DIR", Path(tmp)):
                with patch("pipelines.audio_pipeline.time.time") as now:
                    with patch("pipelines.audio_pipeline.record_waveform") as record_waveform:
                        with patch("pipelines.audio_pipeline.subprocess.run") as run:
                            ticks = iter([100.0, 100.0, 102.0, 102.0, 102.0, 102.0, 102.0, 102.0])
                            now.side_effect = lambda: next(ticks, 102.0)
                            record_waveform.return_value = (np.zeros(32000, dtype=np.float32), 16000)
                            run.return_value.returncode = 0
                            run.return_value.stdout = ""
                            run.return_value.stderr = ""

                            start = client.post("/api/audio/playback/capture/start")
                            stop = client.post("/api/audio/playback/capture/stop", json={"volume": 1.0})
                            play = client.post(
                                "/api/audio/playback/play",
                                json={"output_device": "default", "volume": 1.0},
                            )
                            thread = runtime.audio.pipeline._playback_thread
                            if thread is not None:
                                thread.join(timeout=2)

        self.assertEqual(start.status_code, 200)
        self.assertEqual(stop.status_code, 200)
        self.assertEqual(play.status_code, 200)
        self.assertIs(play.get_json()["ok"], True)
        self.assertEqual(play.get_json()["result"]["output_device"], "plughw:CARD=UACDemoV10,DEV=0")
        record_waveform.assert_called_once()


    def test_event_inputs_and_routes(self) -> None:
        from services.event_log import EventLog

        runtime = make_runtime()
        with tempfile.TemporaryDirectory() as tmp:
            runtime.events = EventLog(Path(tmp) / "events.json")
            now = time.time()
            face_hidden = [[10.0, 10.0, 0.0]] * 3 + [[10.0, 10.0, 0.9]] * 14  # nose/eyes missing, ears visible
            latest = {"updated_at": now, "captured_at": now, "frame_size": [320, 180], "persons": [{"keypoints": face_hidden}]}
            runtime.video.vision.latest_result = lambda: latest
            runtime.audio.pipeline.latest_yamnet = {"status": "ok", "updated_at": now, "crying": True, "cry_score": 0.8}

            active, state, details = runtime.event_inputs(now)
            self.assertEqual(state, "covered")
            self.assertEqual(active, {"face_cover_rollover": True, "no_person": False, "boundary": False, "cry": True})
            self.assertEqual(details["cry"]["score"], 0.8)

            # stale pose result and LLM pause both make vision unknown; cry is still read
            latest["updated_at"] = now - 30
            active, state, _ = runtime.event_inputs(now)
            self.assertEqual((state, active["face_cover_rollover"], active["cry"]), ("stale", None, True))
            latest["updated_at"] = now
            runtime.vision.pause("assistant")
            self.assertEqual(runtime.event_inputs(now)[1], "paused")
            runtime.vision.resume("assistant")

            for second in range(4):
                runtime.observe_events(now + second)
            client = create_main_app(runtime).test_client()
            listed = client.get("/api/events?limit=10").get_json()
            self.assertIs(listed["ok"], True)
            self.assertEqual(sorted(e["type"] for e in listed["result"]), ["cry", "face_cover_rollover"])
            only_cry = client.get("/api/events?type=cry").get_json()["result"]
            self.assertEqual([e["type"] for e in only_cry], ["cry"])
            summary = client.get("/api/events/summary?window_s=3600").get_json()["result"]
            self.assertEqual(summary["types"]["cry"]["count"], 1)
            self.assertEqual(summary["current"]["vision_state"], "covered")
            bad = client.get("/api/events?type=sneeze")
            self.assertEqual(bad.status_code, 400)
            self.assertIs(bad.get_json()["ok"], False)
            self.assertEqual(client.get("/api/events/summary?window_s=5").status_code, 400)

    def test_assistant_routes(self) -> None:
        from tests.test_chat_assistant import FakeOllama

        runtime = make_runtime()
        with tempfile.TemporaryDirectory() as tmp:
            runtime.assistant.client = FakeOllama()
            runtime.assistant.pauser.voice_pause_path = Path(tmp) / "voice_pause.json"
            client = create_main_app(runtime).test_client()

            status = client.get("/api/assistant/status").get_json()
            self.assertIs(status["ok"], True)
            self.assertIs(status["result"]["available"], True)
            self.assertIs(status["result"]["llm"]["ok"], True)
            self.assertIs(status["result"]["busy"], False)

            response = client.post("/api/assistant/chat", json={"stream": True, "messages": [{"role": "user", "content": "宝宝吐奶怎么办？"}]})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.mimetype.endswith("ndjson"))
            lines = [json.loads(line) for line in response.get_data(as_text=True).splitlines()]
            self.assertEqual(lines[0]["type"], "start")
            self.assertEqual(lines[-1]["type"], "done")
            self.assertFalse(runtime.vision.paused)

            bad = client.post("/api/assistant/chat", json={"messages": [{"role": "assistant", "content": "x"}]})
            self.assertEqual(bad.status_code, 400)
            self.assertIs(bad.get_json()["ok"], False)
            long = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "问" * 501}]})
            self.assertIn("longer than", long.get_json()["error"])

            plain = client.post("/api/assistant/chat", json={"stream": False, "messages": [{"role": "user", "content": "q"}]})
            self.assertEqual(plain.get_json()["result"]["reply"], "吐奶很常见，竖抱拍嗝即可。")

            runtime.assistant.settings = type(runtime.assistant.settings)(enabled=False)
            off = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "q"}]})
            self.assertEqual(off.status_code, 503)


if __name__ == "__main__":
    unittest.main()
