from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai.audio_ai import YamnetCryDetector, record_waveform


def main() -> None:
    parser = argparse.ArgumentParser(description="Record audio and run YAMNet cry detection")
    parser.add_argument("--seconds", type=float, default=2.0)
    parser.add_argument("--device", default="plughw:0,0")
    parser.add_argument("--output", default="runtime/audio/yamnet_test.wav")
    parser.add_argument("--model", default="models/yamnet/yamnet.onnx")
    parser.add_argument("--labels", default="models/yamnet/yamnet_class_map.csv")
    args = parser.parse_args()

    waveform, rate = record_waveform(args.seconds, args.device, Path(args.output))
    detector = YamnetCryDetector(args.model, args.labels)
    print(json.dumps(detector.infer_waveform(waveform, rate), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
