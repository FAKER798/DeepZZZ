#!/usr/bin/env bash
# Download the voice-trigger ASR models into /home/z/models/asr (or $1).
#   SenseVoice-Small int8 (sherpa-onnx export, ~240 MB) + Silero VAD (~0.6 MB)
# Uses hf-mirror.com by default; override with HF_ENDPOINT=https://huggingface.co
set -euo pipefail

DEST="${1:-$(cd "$(dirname "$0")/.." && pwd)/models/asr}"
HF="${HF_ENDPOINT:-https://hf-mirror.com}"
SV_REPO="csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
VAD_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"

mkdir -p "$DEST/sense-voice"

fetch() {  # url output
  if [ -s "$2" ]; then echo "exists: $2"; return; fi
  echo "download: $1"
  curl -fL --retry 3 -o "$2.part" "$1"
  mv "$2.part" "$2"
}

fetch "$HF/$SV_REPO/resolve/main/tokens.txt" "$DEST/sense-voice/tokens.txt"
fetch "$HF/$SV_REPO/resolve/main/model.int8.onnx" "$DEST/sense-voice/model.int8.onnx"
fetch "$HF/$SV_REPO/resolve/main/test_wavs/zh.wav" "$DEST/sense-voice/test_zh.wav"
fetch "$VAD_URL" "$DEST/silero_vad.onnx"

ls -la "$DEST" "$DEST/sense-voice"
echo "ok. quick test:"
echo "  python3 $(dirname "$0")/../services/voice_trigger.py --test-wav $DEST/sense-voice/test_zh.wav"
