from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from k1edge.config import parse_config


class ConfigTests(unittest.TestCase):
    def test_config_precedence_file_env_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "port": 9000,
                        "analysis_fps": 4,
                        "enable_pose": False,
                    }
                ),
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "PORT": "9001",
                "ENABLE_POSE": "1",
                "AUDIO_OUTPUT_DEVICE": "plughw:1,0",
                "AUDIO_PLAYBACK_VOLUME": "1.5",
            }
            with patch.dict(os.environ, env, clear=True):
                config = parse_config(["--port", "9002", "--analysis-fps", "12"], config_path=config_path)

        self.assertEqual(config.server.port, 9002)
        self.assertEqual(config.video.analysis_fps, 12)
        self.assertIs(config.ai.enable_pose, True)
        self.assertEqual(config.audio.audio_output_device, "plughw:1,0")
        self.assertEqual(config.audio.audio_playback_volume, 1.5)

    def test_namespace_keeps_legacy_device_alias(self) -> None:
        config = parse_config(["--device", "/dev/video9", "--no-camera"])
        namespace = config.to_namespace()

        self.assertEqual(namespace.camera_device, "/dev/video9")
        self.assertEqual(namespace.device, "/dev/video9")
        self.assertIs(namespace.start_camera, False)


if __name__ == "__main__":
    unittest.main()
