from __future__ import annotations

import signal
import threading
from http import HTTPStatus
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_file, stream_with_context
from werkzeug.exceptions import HTTPException

from k1edge.config import EdgeConfig, parse_config
from k1edge.runtime import AppRuntime
from services.care_log import CareError
from services.chat_assistant import AssistantError
from services.event_log import EventLogError
from utils.paths import AUDIO_DIR, DATASET_DIR, LULLABY_DIR, STATIC_DIR, TEMPLATES_DIR, ensure_runtime_dirs


def create_app_runtime(config: EdgeConfig | None = None) -> AppRuntime:
    ensure_runtime_dirs()
    return AppRuntime(config or parse_config())


def create_main_app(runtime: AppRuntime) -> Flask:
    app = Flask("deepzzz_k1_edge", static_folder=None)
    register_errors(app)

    @app.get("/")
    def index():
        return send_allowed_file(TEMPLATES_DIR / "index.html", [TEMPLATES_DIR], "text/html; charset=utf-8")

    @app.get("/static/<path:name>")
    def static_file(name: str):
        return send_allowed_file(STATIC_DIR / name, [STATIC_DIR])

    @app.get("/audio/<path:name>")
    def audio_file(name: str):
        return send_allowed_file(AUDIO_DIR / name, [AUDIO_DIR])

    @app.get("/lullabies/<path:name>")
    def lullaby_file(name: str):
        return send_allowed_file(LULLABY_DIR / name, [LULLABY_DIR])

    @app.get("/api/audio/live.pcm")
    def live_audio_pcm():
        runtime.ensure_live_audio_capture()
        position = runtime.live_audio_position()

        def generate():
            nonlocal position
            while True:
                data, position = runtime.read_live_pcm_since(position, max_bytes=320, timeout=0.25)
                if data:
                    yield data

        response = Response(generate(), mimetype="application/octet-stream")
        response.headers["X-Audio-Sample-Rate"] = "16000"
        response.headers["X-Audio-Channels"] = "1"
        response.headers["X-Audio-Encoding"] = "pcm_s16le"
        return no_store(response)

    @app.get("/api/healthz")
    def healthz():
        return ok(runtime.healthz())

    @app.get("/api/status")
    def status():
        return jsonify(runtime.status())

    @app.get("/api/detections")
    def detections():
        return ok({"result": runtime.vision.latest_result()})

    @app.get("/api/vision/safety")
    def vision_safety():
        return ok({"result": runtime.vision_safety()})

    @app.get("/api/activity-zone")
    def activity_zone():
        return no_store(ok({"result": runtime.activity_zone()}))

    @app.post("/api/activity-zone")
    def set_activity_zone():
        try:
            return ok({"result": runtime.set_activity_zone(request_json())})
        except ValueError as exc:
            return error(str(exc), HTTPStatus.BAD_REQUEST)

    @app.delete("/api/activity-zone")
    def clear_activity_zone():
        return ok({"result": runtime.clear_activity_zone()})

    @app.get("/api/alerts")
    def alerts():
        return no_store(jsonify(runtime.alerts()))

    @app.get("/api/lullabies")
    def lullabies():
        return no_store(ok({"result": runtime.lullaby_status()}))

    @app.get("/api/voice-trigger")
    def voice_trigger_status():
        return ok({"result": runtime.voice_trigger_status()})

    @app.post("/api/voice-trigger")
    def set_voice_trigger():
        body = request_json()
        if not isinstance(body.get("enabled"), bool):
            return error("body must be {\"enabled\": true|false}", HTTPStatus.BAD_REQUEST)
        return ok({"result": runtime.set_voice_trigger_enabled(body["enabled"])})

    # ---- baby care records (naviga app Care page + voice worker)
    @app.errorhandler(CareError)
    def handle_care_error(exc: CareError):
        return error(str(exc), exc.status)

    @app.get("/api/care/records")
    def care_records():
        args = request.args
        return ok(
            {
                "result": runtime.care.list_records(
                    since=args.get("since"),
                    record_type=args.get("type"),
                    limit=args.get("limit"),
                    until=args.get("until"),
                )
            }
        )

    @app.post("/api/care/records")
    def create_care_record():
        return ok({"result": runtime.care.create_record(request_json(), source="app")})

    @app.get("/api/care/records/<record_id>")
    def get_care_record(record_id: str):
        return ok({"result": runtime.care.get_record(record_id)})

    @app.route("/api/care/records/<record_id>", methods=["POST", "PUT", "PATCH"])
    def update_care_record(record_id: str):
        return ok({"result": runtime.care.update_record(record_id, request_json())})

    @app.delete("/api/care/records/<record_id>")
    def delete_care_record(record_id: str):
        return ok({"result": runtime.care.delete_record(record_id)})

    @app.post("/api/care/sleep/end")
    def end_care_sleep():
        body = request_json()
        return ok({"result": runtime.care.end_sleep(end_at=body.get("end_at"), record_id=body.get("id"))})

    @app.get("/api/care/overview")
    def care_overview():
        return ok({"result": runtime.care.overview()})

    @app.get("/api/care/profile")
    def care_profile():
        return ok({"result": runtime.care.profile()})

    @app.route("/api/care/profile", methods=["POST", "PUT", "PATCH"])
    def save_care_profile():
        return ok({"result": runtime.care.save_profile(request_json())})

    # ---- vision / sound episodes (naviga app; also summarised into the AI Q&A context)
    @app.errorhandler(EventLogError)
    def handle_event_log_error(exc: EventLogError):
        return error(str(exc), exc.status)

    @app.get("/api/events")
    def events():
        args = request.args
        return ok(
            {
                "result": runtime.events.list_events(
                    since=args.get("since"),
                    until=args.get("until"),
                    event_type=args.get("type"),
                    limit=args.get("limit"),
                )
            }
        )

    @app.get("/api/events/summary")
    def events_summary():
        return ok({"result": runtime.events.summary(window_s=request.args.get("window_s", 7200))})

    # ---- AI Q&A (naviga app): local Ollama model, YOLO + ASR paused while it answers
    @app.errorhandler(AssistantError)
    def handle_assistant_error(exc: AssistantError):
        return error(str(exc), exc.status)

    @app.get("/api/assistant/status")
    def assistant_status():
        return ok({"result": runtime.assistant.status()})

    @app.post("/api/assistant/chat")
    def assistant_chat():
        body = request_json()
        messages = runtime.assistant.prepare(body)
        if body.get("stream") is False:
            return ok({"result": runtime.assistant.reply(messages)})
        response = Response(
            stream_with_context(runtime.assistant.stream(messages)),
            mimetype="application/x-ndjson",
        )
        response.headers["X-Accel-Buffering"] = "no"
        return no_store(response)

    @app.get("/api/light")
    def light_status():
        return ok({"light": runtime.hardware.light.status()})

    @app.get("/api/temperature-humidity")
    @app.get("/api/temperature_humidity")
    def temperature_humidity_status():
        return ok({"temperature_humidity": runtime.hardware.temperature_humidity.status()})

    @app.get("/api/snapshot")
    def snapshot():
        jpg = runtime.vision.latest_jpeg()
        if jpg is None:
            return error("no analysis frame yet", HTTPStatus.NOT_FOUND)
        return no_store(Response(jpg, mimetype="image/jpeg"))

    @app.get("/api/preview-snapshot")
    def preview_snapshot():
        jpg = runtime.camera.latest_preview_jpeg()
        if jpg is None:
            return error("no preview frame yet", HTTPStatus.NOT_FOUND)
        return no_store(Response(jpg, mimetype="image/jpeg"))

    @app.post("/api/camera/start")
    def camera_start():
        runtime.camera.start()
        return ok({"camera": runtime.camera.status()})

    @app.post("/api/camera/stop")
    def camera_stop():
        runtime.camera.stop()
        return ok({"camera": runtime.camera.status()})

    @app.post("/api/vision/start")
    def vision_start():
        runtime.vision.start()
        return ok({"vision": runtime.vision.status()})

    @app.post("/api/vision/stop")
    def vision_stop():
        runtime.vision.stop()
        return ok({"vision": runtime.vision.status()})

    @app.post("/api/audio/record")
    def record_audio():
        body = request_json()
        return ok({"result": runtime.record_audio(float(body.get("seconds", 2.0)))})

    @app.post("/api/audio/yamnet")
    def analyze_cry():
        body = request_json()
        return ok({"result": runtime.analyze_cry(float(body.get("seconds", 2.0)))})

    @app.post("/api/audio/playback")
    def play_recent_audio():
        body = request_json()
        output_device = body.get("output_device")
        return ok(
            {
                "result": runtime.play_recent_audio(
                    float(body.get("seconds", 2.0)),
                    output_device=str(output_device) if output_device not in (None, "") else None,
                    volume=body.get("volume"),
                )
            }
        )

    @app.post("/api/audio/playback/capture/start")
    def start_playback_capture():
        return ok({"result": runtime.start_playback_capture()})

    @app.post("/api/audio/playback/capture/stop")
    def stop_playback_capture():
        body = request_json()
        return ok({"result": runtime.stop_playback_capture(volume=body.get("volume"))})

    @app.post("/api/audio/playback/play")
    def play_captured_audio():
        body = request_json()
        output_device = body.get("output_device")
        return ok(
            {
                "result": runtime.play_captured_audio(
                    output_device=str(output_device) if output_device not in (None, "") else None,
                    volume=body.get("volume"),
                )
            }
        )

    @app.post("/api/audio/playback/upload")
    def play_uploaded_audio():
        output_device = request.args.get("output_device")
        volume = request.args.get("volume")
        loop = request.args.get("loop", "").lower() in {"1", "true", "yes", "on"}
        return ok(
            {
                "result": runtime.play_uploaded_audio(
                    request.get_data(cache=False),
                    output_device=str(output_device) if output_device not in (None, "") else None,
                    volume=volume,
                    loop=loop,
                )
            }
        )

    @app.post("/api/lullabies/upload")
    def upload_lullaby():
        title = request.args.get("title")
        volume = request.args.get("volume")
        return ok(
            {
                "result": runtime.save_lullaby(
                    request.get_data(cache=False),
                    title=title,
                    volume=volume,
                )
            }
        )

    @app.post("/api/lullabies/play")
    def play_lullaby():
        body = request_json()
        return ok(
            {
                "result": runtime.play_lullaby(
                    track_id=body.get("track_id"),
                    volume=body.get("volume"),
                    position_ms=body.get("position_ms"),
                    loop=body.get("loop", True) is not False,
                )
            }
        )

    @app.post("/api/lullabies/pause")
    def pause_lullaby():
        return ok({"result": runtime.pause_lullaby()})

    @app.post("/api/lullabies/seek")
    def seek_lullaby():
        body = request_json()
        return ok({"result": runtime.seek_lullaby(body.get("position_ms"))})

    @app.post("/api/audio/playback/volume")
    def set_playback_volume():
        body = request_json()
        return ok({"result": runtime.set_playback_volume(body.get("volume"))})

    @app.post("/api/audio/playback/stop")
    def stop_uploaded_audio():
        return ok({"result": runtime.stop_playback()})

    @app.post("/api/audio/talk/start")
    def talk_start():
        return ok({"result": runtime.start_talk_input()})

    @app.post("/api/audio/talk/chunk")
    def talk_chunk():
        return ok({"result": runtime.receive_talk_pcm(request.get_data(cache=False))})

    @app.post("/api/audio/talk/stop")
    def talk_stop():
        return ok({"result": runtime.stop_talk_input()})

    @app.post("/api/light")
    def set_light():
        body = request_json()
        try:
            light = runtime.set_light_duty(float(body.get("duty", 0.0)), frequency_hz=body.get("frequency_hz"))
        except (TypeError, ValueError) as exc:
            return error(str(exc), HTTPStatus.BAD_REQUEST)
        return ok({"light": light})

    return app


def create_dataset_app(runtime: AppRuntime) -> Flask:
    app = Flask("deepzzz_k1_dataset", static_folder=None)
    register_errors(app)

    @app.before_request
    def require_dataset_enabled():
        if not runtime.config.dataset.enable_dataset_capture:
            return error("dataset capture is disabled", HTTPStatus.FORBIDDEN)
        return None

    @app.get("/")
    def index():
        return send_allowed_file(TEMPLATES_DIR / "dataset.html", [TEMPLATES_DIR], "text/html; charset=utf-8")

    @app.get("/static/<path:name>")
    def static_file(name: str):
        return send_allowed_file(STATIC_DIR / name, [STATIC_DIR])

    @app.get("/dataset/<path:name>")
    def dataset_file(name: str):
        return send_allowed_file(DATASET_DIR / name, [DATASET_DIR])

    @app.get("/api/dataset/stats")
    def dataset_stats_get():
        return ok({"result": runtime.dataset_stats()})

    @app.post("/api/dataset/stats")
    def dataset_stats_post():
        return ok({"result": runtime.dataset_stats()})

    @app.get("/api/snapshot")
    def snapshot():
        jpg = runtime.vision.latest_jpeg()
        if jpg is None:
            return error("no analysis frame yet", HTTPStatus.NOT_FOUND)
        return no_store(Response(jpg, mimetype="image/jpeg"))

    @app.get("/api/raw-snapshot")
    def raw_snapshot():
        jpg = runtime.vision.latest_raw_jpeg()
        if jpg is None:
            return error("no raw analysis frame yet", HTTPStatus.NOT_FOUND)
        return no_store(Response(jpg, mimetype="image/jpeg"))

    @app.post("/api/dataset/capture")
    def capture():
        return ok({"result": runtime.capture_dataset_sample(request_json().get("labels"))})

    @app.post("/api/dataset/confirm")
    def confirm():
        return ok({"result": runtime.confirm_dataset_sample(str(request_json().get("id", "")))})

    @app.post("/api/dataset/cancel")
    def cancel():
        return ok({"result": runtime.delete_dataset_sample(str(request_json().get("id", "")))})

    return app


def serve(runtime: AppRuntime) -> None:
    main_app = create_main_app(runtime)
    runtime.start()
    install_signal_handlers(runtime)
    if runtime.config.dataset.enable_dataset_capture:
        dataset_app = create_dataset_app(runtime)
        threading.Thread(
            target=lambda: dataset_app.run(
                host=runtime.config.server.host,
                port=runtime.config.server.dataset_capture_port,
                threaded=True,
                use_reloader=False,
            ),
            name="dataset-http",
            daemon=True,
        ).start()
    try:
        main_app.run(
            host=runtime.config.server.host,
            port=runtime.config.server.port,
            threaded=True,
            use_reloader=False,
        )
    finally:
        runtime.stop()


def install_signal_handlers(runtime: AppRuntime) -> None:
    def shutdown(signum: int, frame: Any) -> None:
        runtime.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)


def register_errors(app: Flask) -> None:
    @app.errorhandler(Exception)
    def handle_exception(exc: Exception):
        if isinstance(exc, HTTPException):
            return error(exc.description, exc.code or HTTPStatus.INTERNAL_SERVER_ERROR)
        return error(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)


def ok(payload: dict[str, Any]) -> Response:
    return no_store(jsonify({"ok": True, **payload}))


def error(message: str, status: int | HTTPStatus = HTTPStatus.INTERNAL_SERVER_ERROR) -> tuple[Response, int]:
    response = no_store(jsonify({"ok": False, "error": message}))
    return response, int(status)


def request_json() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def send_allowed_file(path: Path, allowed_roots: list[Path], content_type: str | None = None):
    resolved = path.resolve()
    roots = [root.resolve() for root in allowed_roots]
    if not any(resolved == root or root in resolved.parents for root in roots):
        return error("forbidden", HTTPStatus.FORBIDDEN)
    if not resolved.exists() or not resolved.is_file():
        return error("not found", HTTPStatus.NOT_FOUND)
    if resolved.suffix == ".m3u8":
        playlist = resolved.read_text(encoding="utf-8").replace(
            "#EXT-X-TARGETDURATION:0",
            "#EXT-X-TARGETDURATION:1",
        )
        response = Response(playlist, mimetype=content_type or "application/vnd.apple.mpegurl")
    else:
        response = send_file(resolved, mimetype=content_type, conditional=False)
    if resolved.suffix in {".m3u8", ".ts"} or STATIC_DIR.resolve() in resolved.parents:
        return no_store(response)
    return response


def no_store(response):
    if isinstance(response, tuple):
        no_store(response[0])
        return response
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response
