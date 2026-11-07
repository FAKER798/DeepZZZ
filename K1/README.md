# K1 Edge Project

Unified DeepZZZ K1 board service.

## Current Architecture

```text
main.py
  -> k1edge.config typed config
  -> k1edge.runtime AppRuntime
  -> k1edge.app Flask HTTP apps
```

The external surface is intentionally stable:

- Main web/API service: `http://<board>:7860/`
- Dataset capture service: `http://<board>:7861/`
- Main systemd service: `deepzzz-k1-edge.service`
- Worker services: `day-night-sync.service`, `light-pwm.service`, `temperature-humodity.service`

## Source Layout

```text
K1/
├── main.py                  # application entry point
├── k1edge/                  # typed config, runtime facade, Flask apps, runtime store
├── devices/                 # hardware capture/control and worker CLIs
├── ai/                      # model inference and provider boundary
├── pipelines/               # video/audio pipeline wiring
├── services/                # resource, activity-zone, and dataset services
├── web/                     # templates and static assets
├── models/                  # YAMNet assets
├── .brdk_models/pose/       # active K1 pose model
├── scripts/                 # board scripts and systemd units
├── tests/                   # no-hardware unittest coverage
└── runtime/                 # generated files, not committed
```

## Runtime Flow

Video:

```text
devices.camera -> pipelines.video_pipeline -> ai.vision_ai
  -> k1edge.runtime.VideoRuntime
  -> k1edge.app (/api/preview-snapshot, /api/detections, /api/snapshot, /api/status)
```

Audio:

```text
ai.audio_ai ContinuousWaveformRecorder -> pipelines.audio_pipeline
  -> k1edge.runtime.AudioRuntime
  -> k1edge.app (/api/status, /api/audio/yamnet)
```

Audio playback validation:

```text
web Audio Playback controls
  -> POST /api/audio/playback
  -> latest recorder buffer or temporary arecord capture
  -> runtime/audio/playback_<timestamp>.wav
  -> aplay -D <AUDIO_OUTPUT_DEVICE>
```

The production input and output device is the USB audio adapter at `plughw:CARD=UACDemoV10,DEV=0`.

Hardware workers:

```text
web UI -> POST /api/light -> runtime/state/light_command.json
  -> light-pwm.service -> devices.lightcontrol.HardwarePwmWorker -> PWM sysfs

temperature-humodity.service -> devices.temperature_humidity.TemperatureHumidityWorker
  -> runtime/state/temperature_humidity.json -> /api/status

day-night-sync.service -> scripts/day_night.py sync-ircut
  -> runtime/state/day_night + runtime/state/current_mode -> /api/status
```

The main web service must not directly write PWM sysfs, GPIO pinmux, or DHT11 timing. It writes command/state files and reads worker status.

## Configuration

Configuration priority is:

```text
defaults -> config.json -> environment variables -> CLI flags
```

The typed configuration implementation lives in `k1edge.config`.

Current board defaults in `config.json` and `scripts/deepzzz-k1-edge.service` use:

```text
POSE_MODEL=/home/z/.brdk_models/pose/yolov8n-pose-k1-320-int8.onnx
POSE_PROVIDER=SpaceMITExecutionProvider
POSE_PROVIDER_FALLBACK=0
ANALYSIS_WIDTH=320
ANALYSIS_FPS=10
ENABLE_DATASET_CAPTURE=1
```

## API

Main service:

- `/`
- `/static/*`
- `/api/preview-snapshot`
- `/audio/*`
- `/api/healthz`
- `/api/status`
- `/api/detections`
- `/api/snapshot`
- `/api/light`
- `/api/temperature-humidity`
- `/api/audio/record`
- `/api/audio/yamnet`
- `/api/audio/playback`

Dataset service:

- `/`
- `/static/*`
- `/dataset/*`
- `/api/dataset/stats`
- `/api/dataset/capture`
- `/api/dataset/confirm`
- `/api/dataset/cancel`
- `/api/snapshot`
- `/api/raw-snapshot`

## Validation

No-hardware checks:

```bash
python -m compileall -q .
python -m unittest discover -v
```

Board checks:

```bash
sudo systemctl restart deepzzz-k1-edge light-pwm temperature-humodity day-night-sync
curl http://127.0.0.1:7860/api/healthz
curl http://127.0.0.1:7860/api/status
curl http://127.0.0.1:7860/api/light
curl -X POST http://127.0.0.1:7860/api/audio/playback -H 'Content-Type: application/json' -d '{"seconds":2,"volume":1.0,"output_device":"default"}'
curl http://127.0.0.1:7861/api/dataset/stats
```
