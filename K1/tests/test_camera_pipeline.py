from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import cv2

from devices.camera import CameraPipeline


class CameraPipelineCommandTests(unittest.TestCase):
    def make_pipeline(self, root: Path) -> CameraPipeline:
        return CameraPipeline(
            log_dir=root / "logs",
            frame_callback=lambda frame, meta: None,
            analysis_width=320,
            analysis_fps=20,
        )

    def test_capture_process_only_drains_independent_mjpeg_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            command = self.make_pipeline(Path(tmp))._ffmpeg_command("/dev/video0")

        self.assertIn("mjpeg", command)
        self.assertNotIn("h264", command)
        self.assertIn("image2pipe", command)
        self.assertIn("pipe:1", command)
        self.assertIn("copy", command)
        self.assertNotIn("-vf", command)
        self.assertFalse(any(value in command for value in ("hls", "mpegts")))
        self.assertFalse(any(value.startswith(("tcp://", "udp://")) for value in command))

    def test_running_ffmpeg_warning_is_logged_without_setting_camera_error(self) -> None:
        class RunningProcess:
            stderr = io.BytesIO(b"Packet corrupt, dropping it\n")

            @staticmethod
            def poll():
                return None

        with tempfile.TemporaryDirectory() as tmp:
            pipeline = self.make_pipeline(Path(tmp))
            pipeline.log_dir.mkdir(parents=True)
            pipeline._read_stderr(RunningProcess(), "analysis.log", "analysis")

        self.assertIsNone(pipeline.status()["error"])

    def test_source_reader_keeps_only_the_latest_complete_jpeg(self) -> None:
        _, first = cv2.imencode(".jpg", np.full((8, 8, 3), 17, dtype=np.uint8))
        _, second = cv2.imencode(".jpg", np.full((8, 8, 3), 231, dtype=np.uint8))

        with tempfile.TemporaryDirectory() as tmp:
            pipeline = self.make_pipeline(Path(tmp))
            process = SimpleNamespace(stdout=io.BytesIO(first.tobytes() + second.tobytes()))
            pipeline._read_source_pipe(process, pipeline._generation)

        self.assertEqual(pipeline._source_sequence, 2)
        self.assertEqual(pipeline._source_jpeg, second.tobytes())
        # The high-res preview is the camera's own JPEG, passed through without re-encoding.
        self.assertEqual(pipeline.latest_preview_jpeg(), second.tobytes())

    def test_reader_of_a_replaced_capture_process_stops_writing(self) -> None:
        _, frame = cv2.imencode(".jpg", np.full((8, 8, 3), 17, dtype=np.uint8))

        with tempfile.TemporaryDirectory() as tmp:
            pipeline = self.make_pipeline(Path(tmp))
            stale_generation = pipeline._generation
            pipeline._generation += 1  # the watchdog restarted ffmpeg
            pipeline._read_source_pipe(SimpleNamespace(stdout=io.BytesIO(frame.tobytes())), stale_generation)

        self.assertEqual(pipeline._source_sequence, 0)
        self.assertIsNone(pipeline.latest_preview_jpeg())

    def test_latest_source_frame_becomes_low_res_analysis_frame(self) -> None:
        frame = np.full((720, 1280, 3), 91, dtype=np.uint8)
        _, encoded = cv2.imencode(".jpg", frame)
        received = []

        with tempfile.TemporaryDirectory() as tmp:
            pipeline = self.make_pipeline(Path(tmp))
            pipeline.frame_callback = lambda image, meta: received.append((image, meta))
            self.assertTrue(pipeline._process_source_jpeg(encoded.tobytes(), 123.0))

        self.assertEqual(received[0][0].shape, (180, 320, 3))
        self.assertEqual(received[0][1]["captured_at"], 123.0)

if __name__ == "__main__":
    unittest.main()
