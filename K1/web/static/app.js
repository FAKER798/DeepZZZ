const statusEl = document.getElementById("status");
const snapshotEl = document.getElementById("snapshot");
const videoEl = document.getElementById("video");
const overlayEl = document.getElementById("overlay");
const zoneOverlayEl = document.getElementById("zoneOverlay");
const poseModelEl = document.getElementById("poseModel");
const sleepStateEl = document.getElementById("sleepState");
const videoAgeSummaryEl = document.getElementById("videoAgeSummary");
const videoFpsSummaryEl = document.getElementById("videoFpsSummary");
const audioRateSummaryEl = document.getElementById("audioRateSummary");
const audioAgeSummaryEl = document.getElementById("audioAgeSummary");
const cpuSummaryEl = document.getElementById("cpuSummary");
const memorySummaryEl = document.getElementById("memorySummary");
const npuSummaryEl = document.getElementById("npuSummary");
const dayNightSummaryEl = document.getElementById("dayNightSummary");
const temperatureSummaryEl = document.getElementById("temperatureSummary");
const humiditySummaryEl = document.getElementById("humiditySummary");
const lightDutyTextEl = document.getElementById("lightDutyText");
const lightSliderEl = document.getElementById("lightSlider");
const lightDutyInputEl = document.getElementById("lightDutyInput");
const lightStatusEl = document.getElementById("lightStatus");
const audioPlaybackStateEl = document.getElementById("audioPlaybackState");
const audioPlaybackStatusEl = document.getElementById("audioPlaybackStatus");
const audioPlaybackLinkEl = document.getElementById("audioPlaybackLink");
const playbackVolumeEl = document.getElementById("playbackVolume");
const playbackVolumeRangeEl = document.getElementById("playbackVolumeRange");
const playbackVolumeValueEl = document.getElementById("playbackVolumeValue");
const playbackDeviceEl = document.getElementById("playbackDevice");
const captureButtonEl = document.getElementById("captureButton");
const playbackButtonEl = document.getElementById("playbackButton");

const deviceFields = {
  camera: document.getElementById("deviceCamera"),
  video: document.getElementById("deviceVideo"),
  analysis: document.getElementById("deviceAnalysis"),
  mic: document.getElementById("deviceMic"),
  audio: document.getElementById("deviceAudio"),
  cry: document.getElementById("deviceCry"),
};

const resourceFields = {
  cpu: document.getElementById("resourceCpu"),
  memory: document.getElementById("resourceMemory"),
  app: document.getElementById("resourceApp"),
  ffmpeg: document.getElementById("resourceFfmpeg"),
};

const yamnetFields = {
  status: document.getElementById("yamnetStatus"),
  cryScore: document.getElementById("yamnetCryScore"),
  noise: document.getElementById("yamnetNoise"),
  window: document.getElementById("yamnetWindow"),
  top: document.getElementById("yamnetTop"),
  latency: document.getElementById("yamnetLatency"),
};

const cryPanelEl = document.getElementById("cryDetectionPanel");
const cryLoopStateEl = document.getElementById("cryLoopState");
const voicePanelEl = document.getElementById("voiceTriggerPanel");
const voiceToggleEl = document.getElementById("voiceTriggerToggle");
const voiceFields = {
  state: document.getElementById("voiceTriggerState"),
  heard: document.getElementById("voiceTriggerHeard"),
  action: document.getElementById("voiceTriggerAction"),
  today: document.getElementById("careToday"),
  recent: document.getElementById("careRecent"),
  latency: document.getElementById("voiceTriggerLatency"),
};
let voiceTriggerEnabled = null;
const coverPanelEl = document.getElementById("faceCoverPanel");
const coverFields = {
  status: document.getElementById("coverStatus"),
  reason: document.getElementById("coverReason"),
  visiblePoints: document.getElementById("coverVisiblePoints"),
  nose: document.getElementById("coverNose"),
  leftEye: document.getElementById("coverLeftEye"),
  rightEye: document.getElementById("coverRightEye"),
  leftEar: document.getElementById("coverLeftEar"),
  rightEar: document.getElementById("coverRightEar"),
};

let latestDetections = null;
let previewTimer = null;
let previewRequestedAt = 0;
let previewLoading = false;
let snapshotRefreshMs = 500;
let snapshotTimer = null;
let snapshotRequestedAt = 0;
let snapshotLoading = false;
let lightCommitTimer = null;
let lightPending = false;
let lightEditing = false;
let playbackPending = false;
let playbackValueEditing = false;
let faceCoverAwake = false;
let cryingAwake = false;
let motionAwake = false;
let previousSleepSample = null;
let activityZone = { zone: null, triggered: false };
let zoneBlinkTimer = null;

// HD Preview should currently be a clean video stream only.
// Keep the overlay canvas and drawOverlay() implementation intact so the pose
// boxes/skeleton can be restored later by changing this flag to true.
const ENABLE_HD_POSE_OVERLAY = false;
const SLEEP_MOTION_THRESHOLD_PX = 25;
const SLEEP_SAMPLE_INTERVAL_MS = 1000;

const POSE_CONNECTIONS = [
  [16, 14], [14, 12], [15, 13], [13, 11], [12, 11],
  [5, 7], [7, 9], [6, 8], [8, 10],
  [5, 6], [5, 11], [6, 12],
  [11, 13], [12, 14],
  [0, 1], [0, 2], [1, 3], [2, 4],
  [0, 5], [0, 6],
  [3, 5], [4, 6],
];

const STANDARD_POSE = [
  [130, 28],
  [114, 22],
  [146, 22],
  [96, 33],
  [164, 33],
  [82, 82],
  [178, 82],
  [56, 132],
  [204, 132],
  [44, 186],
  [216, 186],
  [96, 168],
  [164, 168],
  [88, 218],
  [172, 218],
  [78, 250],
  [182, 250],
];

const CONFIDENCE_LABEL_OFFSETS = [
  [8, -17],
  [-48, -10],
  [14, -10],
  [-56, 4],
  [20, 4],
  [-62, -5],
  [20, -5],
  [-54, -4],
  [20, -4],
  [-26, 20],
  [-18, 20],
  [-60, -2],
  [20, -2],
  [-58, 3],
  [20, 3],
  [-52, 2],
  [18, 2],
];

function setStatus(text) {
  if (statusEl) statusEl.textContent = text;
}

const frameObjectUrls = new WeakMap();

async function replaceWithLatestFrame(element, path) {
  const response = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`frame request failed: ${response.status}`);
  const nextUrl = URL.createObjectURL(await response.blob());
  const decoder = new Image();
  decoder.decoding = "async";
  decoder.src = nextUrl;
  try {
    await decoder.decode();
  } catch (error) {
    URL.revokeObjectURL(nextUrl);
    throw error;
  }
  const previousUrl = frameObjectUrls.get(element);
  element.src = nextUrl;
  frameObjectUrls.set(element, nextUrl);
  if (previousUrl) URL.revokeObjectURL(previousUrl);
}

async function refreshPreview() {
  if (previewLoading) return;
  if (document.hidden) {
    schedulePreviewRefresh(250);
    return;
  }
  previewLoading = true;
  previewRequestedAt = performance.now();
  try {
    await replaceWithLatestFrame(videoEl, "/api/preview-snapshot");
    schedulePreviewRefresh();
  } catch (_) {
    schedulePreviewRefresh(100);
  } finally {
    previewLoading = false;
  }
}

function schedulePreviewRefresh(delay = null) {
  if (previewTimer) window.clearTimeout(previewTimer);
  const elapsed = previewRequestedAt > 0 ? performance.now() - previewRequestedAt : 0;
  const interval = 33;
  const nextDelay = delay == null ? Math.max(0, interval - elapsed) : delay;
  previewTimer = window.setTimeout(refreshPreview, nextDelay);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    ...options,
  });
  const payload = await response.json();
  if (!payload.ok) {
    throw new Error(payload.error || `request failed: ${path}`);
  }
  return payload;
}

function renderRuntime(status) {
  snapshotRefreshMs = analysisSnapshotInterval(status.config.analysis_fps);
  deviceFields.camera.textContent = status.camera.running ? `connected (${shortDevice(status.camera.device)})` : "not running";
  deviceFields.video.textContent = status.camera.preview_ready ? "latest frame ready" : "waiting for frame";
  deviceFields.analysis.textContent = status.camera.frames > 0 ? `${status.camera.frames} captured / ${status.vision.processed} processed` : "waiting for frame";
  deviceFields.mic.textContent = status.config.audio_device || "not configured";
  deviceFields.audio.textContent = audioSampleStatus(status.yamnet);
  deviceFields.cry.textContent = status.config.enable_yamnet ? "running" : "disabled";
  yamnetFields.window.textContent = `${status.config.yamnet_seconds}s`;
  renderSummary(status.summary);
  renderDayNight(status.day_night);
  renderTemperatureHumidity(status.temperature_humidity);
  renderLight(status.light);
  renderYamnet(status.yamnet);
  renderAudioPlayback(status.audio_playback, status.config);
  renderActivityZone(status.activity_zone);
  renderVoiceTrigger(status.voice_trigger);
}

const VOICE_STATE_TEXT = {
  starting: "Starting...",
  loading: "Loading ASR model...",
  listening: "Listening",
  speech: "Hearing speech...",
  reconnecting: "Reconnecting to mic stream",
  disabled: "Disabled",
  offline: "Worker offline",
  error: "Error",
  stopped: "Stopped",
};

const VOICE_REASON_TEXT = {
  already_logged: "已在 App 中记录",
  duplicate: "重复，已合并",
  already_sleeping: "已在睡眠中",
  no_sleep_in_progress: "没有进行中的睡眠",
  sleep_longer_than_24h: "睡眠超过 24 小时",
};

const CARE_TYPE_TEXT = { feeding: "喂养", diaper: "尿布", sleep: "睡眠", growth: "生长" };
const CARE_KIND_TEXT = {
  breast: "亲喂", bottle: "瓶喂", solid: "辅食",
  wet: "嘘嘘", dirty: "便便", mixed: "嘘嘘+便便", dry: "干爽",
};

function careRecordText(record) {
  if (!record) return "-";
  const parts = [CARE_TYPE_TEXT[record.type] || record.type];
  if (record.kind) parts.push(CARE_KIND_TEXT[record.kind] || record.kind);
  if (record.amount_ml) parts.push(`${record.amount_ml} ml`);
  if (record.duration_min) parts.push(`${record.duration_min} 分`);
  if (record.food) parts.push(record.food);
  if (record.type === "sleep") {
    parts.push(record.end_at ? `${Math.round((record.end_at - record.start_at) / 60)} 分钟` : "进行中");
  }
  const source = record.source === "voice" ? " 🎙" : "";
  return `${voiceClock(record.start_at)} ${parts.join(" · ")}${source}`;
}

async function refreshCare() {
  if (!voiceFields.today) return;
  try {
    const [overview, records] = await Promise.all([
      api("/api/care/overview"),
      api("/api/care/records?limit=4"),
    ]);
    const today = overview.result.today || {};
    const feeding = today.feeding || {};
    const diaper = today.diaper || {};
    const sleep = today.sleep || {};
    voiceFields.today.textContent =
      `喂养 ${feeding.count || 0} 次${feeding.bottle_ml ? ` (${feeding.bottle_ml} ml)` : ""} · ` +
      `尿布 ${diaper.count || 0} 次 · 睡眠 ${sleep.count || 0} 段 ${Math.round(sleep.total_min || 0)} 分`;
    const items = records.result || [];
    voiceFields.recent.textContent = items.length ? items.map(careRecordText).join("  |  ") : "还没有记录";
  } catch (err) {
    voiceFields.today.textContent = `- (${err.message})`;
  }
}

function voiceClock(seconds) {
  if (!seconds) return "";
  return new Date(seconds * 1000).toLocaleTimeString();
}

function renderVoiceTrigger(voice) {
  if (!voicePanelEl) return;
  voicePanelEl.classList.remove("covered", "clear", "caution");
  if (!voice) {
    voicePanelEl.classList.add("caution");
    voiceFields.state.textContent = "waiting";
    voiceToggleEl.textContent = "-";
    return;
  }
  const state = voice.state || "offline";
  const online = voice.online === true;
  // The worker applies the command file within ~1s; show the requested state meanwhile.
  voiceTriggerEnabled = (voice.requested_enabled ?? voice.enabled) === true;
  voicePanelEl.classList.add(!online || state === "error" ? "covered" : voiceTriggerEnabled ? "clear" : "caution");
  let stateText = VOICE_STATE_TEXT[state] || state;
  if (voice.error && (state === "error" || state === "offline" || state === "reconnecting")) {
    stateText = `${stateText}: ${voice.error}`;
  }
  voiceFields.state.textContent = stateText;
  voiceToggleEl.disabled = !online;
  voiceToggleEl.textContent = voiceTriggerEnabled ? "Disable" : "Enable";

  const last = voice.last_transcript;
  if (last && last.text) {
    const hits = (last.events || []).map((event) => event.keyword).filter(Boolean);
    const hit = hits.length ? ` [${hits.join(", ")}]` : "";
    voiceFields.heard.textContent = `${voiceClock(last.at)} ${last.text}${hit}`;
  } else {
    voiceFields.heard.textContent = "-";
  }
  const recent = (voice.recent_records || [])[0];
  const skipped = last && (last.events || []).find((event) => event.reason);
  if (skipped && (!recent || last.at > recent.at)) {
    const type = CARE_TYPE_TEXT[skipped.type] || skipped.type;
    voiceFields.action.textContent = `${type}: 未记录（${VOICE_REASON_TEXT[skipped.reason] || skipped.reason}）`;
  } else if (recent && recent.record) {
    const verb = recent.result === "merged" ? "更新" : "新增";
    voiceFields.action.textContent = `${verb} ${careRecordText(recent.record)}`;
  } else {
    voiceFields.action.textContent = "-";
  }
  voiceFields.latency.textContent = voice.avg_asr_ms == null
    ? "-"
    : `${voice.avg_asr_ms} ms avg (RTF ${voice.avg_rtf ?? "-"}, ${voice.provider || "cpu"})`;
}

async function toggleVoiceTrigger() {
  if (voiceTriggerEnabled === null) return;
  voiceToggleEl.disabled = true;
  try {
    const payload = await api("/api/voice-trigger", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !voiceTriggerEnabled }),
    });
    voiceTriggerEnabled = payload.result.requested_enabled ?? !voiceTriggerEnabled;
    voiceToggleEl.textContent = voiceTriggerEnabled ? "Disable" : "Enable";
  } catch (err) {
    setStatus(err.message);
  } finally {
    voiceToggleEl.disabled = false;
  }
}

function renderActivityZone(result) {
  if (!result) return;
  activityZone = {
    zone: normalizeZone(result.zone),
    triggered: result.triggered === true,
  };
}

function normalizeZone(zone) {
  if (!zone) return null;
  const firstX = clamp01(zone.left);
  const firstY = clamp01(zone.top);
  const secondX = clamp01(zone.right);
  const secondY = clamp01(zone.bottom);
  const normalized = {
    left: Math.min(firstX, secondX),
    top: Math.min(firstY, secondY),
    right: Math.max(firstX, secondX),
    bottom: Math.max(firstY, secondY),
  };
  if ((normalized.right - normalized.left) * (normalized.bottom - normalized.top) <= 0.001) {
    return null;
  }
  return {
    ...normalized,
    points: [
      { x: normalized.left, y: normalized.top },
      { x: normalized.right, y: normalized.top },
      { x: normalized.right, y: normalized.bottom },
      { x: normalized.left, y: normalized.bottom },
    ],
  };
}

function clamp01(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(1, number));
}

function renderResources(resources) {
  if (!resources) {
    resourceFields.cpu.textContent = "waiting";
    return;
  }
  const memory = resources.memory || {};
  const processes = resources.processes || [];
  const appProcess = processes.find((process) => process.name === "main.py");
  const ffmpegProcess = processes.find((process) => process.name === "ffmpeg");
  resourceFields.cpu.textContent = resources.cpu_percent == null ? "warming up" : `${resources.cpu_percent}% / ${resources.cpu_count} cores`;
  resourceFields.memory.textContent = memory.total_mb ? `${memory.used_mb} / ${memory.total_mb} MB (${memory.percent}%)` : "-";
  resourceFields.app.textContent = formatProcess(appProcess);
  resourceFields.ffmpeg.textContent = formatProcess(ffmpegProcess);
  cpuSummaryEl.textContent = resources.cpu_percent == null ? "-" : `${resources.cpu_percent}%`;
  memorySummaryEl.textContent = memory.percent == null ? "-" : `${memory.percent}%`;
}

function shortDevice(device) {
  if (!device) return "camera";
  const parts = String(device).split("/");
  return parts[parts.length - 1] || device;
}

function audioSampleStatus(yamnet) {
  if (!yamnet) return "waiting for audio";
  if (yamnet.status === "recording" || yamnet.status === "warming") return "recording";
  if (yamnet.status === "error") return "audio error";
  if (yamnet.capture && yamnet.capture.buffer_seconds != null) return `${yamnet.capture.buffer_seconds}s buffered`;
  return "audio captured";
}

function updateSleepState(result) {
  motionAwake = detectPersonBoxMotion(result);
  const awake = motionAwake || faceCoverAwake || cryingAwake;
  const label = awake ? "awake" : "asleep";
  if (!sleepStateEl || sleepStateEl.textContent === label) return;
  sleepStateEl.textContent = label;
  sleepStateEl.classList.toggle("awake", awake);
  sleepStateEl.classList.toggle("asleep", !awake);
}

function detectPersonBoxMotion(result) {
  const corners = personBoxCorners(result);
  const now = Date.now();
  if (!corners) {
    previousSleepSample = null;
    return false;
  }
  if (!previousSleepSample || now - previousSleepSample.time < SLEEP_SAMPLE_INTERVAL_MS) {
    if (!previousSleepSample) {
      previousSleepSample = { time: now, corners };
    }
    return false;
  }
  const moved = cornersMoved(previousSleepSample.corners, corners, SLEEP_MOTION_THRESHOLD_PX);
  previousSleepSample = { time: now, corners };
  return moved;
}

function personBoxCorners(result) {
  const person = result && result.persons && result.persons.length ? result.persons[0] : null;
  const box = person && Array.isArray(person.box) ? person.box : null;
  if (!box || box.length < 4) return null;
  const [x1, y1, x2, y2] = box.map(Number);
  if (![x1, y1, x2, y2].every(Number.isFinite)) return null;
  return [
    [x1, y1],
    [x2, y1],
    [x2, y2],
    [x1, y2],
  ];
}

function cornersMoved(previousCorners, corners, threshold) {
  return corners.some((corner, index) => {
    const previous = previousCorners[index];
    return pointDistance(previous, corner) >= threshold;
  });
}

function pointDistance(a, b) {
  const dx = a[0] - b[0];
  const dy = a[1] - b[1];
  return Math.hypot(dx, dy);
}

function renderSummary(summary) {
  if (!summary) {
    videoAgeSummaryEl.textContent = "-";
    videoFpsSummaryEl.textContent = "-";
    audioAgeSummaryEl.textContent = "-";
    audioRateSummaryEl.textContent = "-";
    npuSummaryEl.textContent = "-";
    temperatureSummaryEl.textContent = "-";
    humiditySummaryEl.textContent = "-";
    return;
  }
  videoAgeSummaryEl.textContent = formatSeconds(summary.video_age_s);
  videoFpsSummaryEl.textContent = formatRate(summary.video_fps, "fps");
  audioAgeSummaryEl.textContent = formatSeconds(summary.audio_age_s);
  audioRateSummaryEl.textContent = formatRate(summary.audio_rate_hz, "/s");
  npuSummaryEl.textContent = summary.npu_percent == null ? "-" : `${summary.npu_percent}%`;
}

function renderTemperatureHumidity(result) {
  if (!result || !result.has_reading) {
    temperatureSummaryEl.textContent = "-";
    humiditySummaryEl.textContent = result && result.connected ? "waiting" : "offline";
    return;
  }
  const temperature = Number(result.temperature_c);
  const humidity = Number(result.humidity_percent);
  temperatureSummaryEl.textContent = Number.isFinite(temperature) ? `${temperature.toFixed(1)}C` : "-";
  humiditySummaryEl.textContent = Number.isFinite(humidity) ? `${humidity.toFixed(1)}%` : "-";
}

function renderDayNight(dayNight) {
  if (!dayNight || !dayNight.state || dayNight.state === "unknown") {
    dayNightSummaryEl.textContent = "-";
    return;
  }
  dayNightSummaryEl.textContent = dayNight.state;
}

function renderLight(light) {
  if (!light) {
    lightDutyTextEl.textContent = "-";
    lightStatusEl.textContent = "waiting";
    return;
  }
  if (light.error) {
    lightStatusEl.textContent = light.error;
  } else {
    lightStatusEl.textContent = light.connected ? "ok" : "offline";
  }
  if (light.duty == null) {
    lightDutyTextEl.textContent = "-";
    return;
  }
  const duty = clampDuty(light.duty);
  lightDutyTextEl.textContent = `${duty}%`;
  if (!lightEditing && !lightPending) {
    lightSliderEl.value = String(duty);
    lightDutyInputEl.value = String(duty);
  }
}

function clampDuty(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(100, Math.round(number)));
}

function queueLightDuty(value) {
  const duty = clampDuty(value);
  lightEditing = true;
  lightSliderEl.value = String(duty);
  lightDutyInputEl.value = String(duty);
  lightDutyTextEl.textContent = `${duty}%`;
  lightStatusEl.textContent = "pending";
  if (lightCommitTimer) {
    window.clearTimeout(lightCommitTimer);
  }
  lightCommitTimer = window.setTimeout(() => commitLightDuty(duty), 180);
}

async function commitLightDuty(value) {
  const duty = clampDuty(value);
  lightPending = true;
  lightStatusEl.textContent = "sending";
  try {
    const payload = await api("/api/light", {
      method: "POST",
      body: JSON.stringify({ duty }),
    });
    renderLight(payload.light);
  } catch (err) {
    lightStatusEl.textContent = err.message;
  } finally {
    lightPending = false;
    lightEditing = false;
  }
}

function analysisSnapshotInterval(fps) {
  const value = Number(fps);
  if (!Number.isFinite(value) || value <= 0) return 1000;
  return Math.max(33, Math.round(1000 / value));
}

function formatSeconds(value) {
  const ageSeconds = Number(value);
  if (!Number.isFinite(ageSeconds) || ageSeconds < 0) return "-";
  if (ageSeconds < 1) return `${Math.round(ageSeconds * 1000)} ms`;
  return `${ageSeconds.toFixed(1)} s`;
}

function formatRate(value, unit) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return `${number.toFixed(1)} ${unit}`;
}

function formatMs(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return `${number.toFixed(0)} ms`;
}

function clampNumber(value, min, max, fallback) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.max(min, Math.min(max, number));
}

function formatProcess(process) {
  if (!process) return "-";
  const cpu = process.cpu_percent == null ? "warming" : `${process.cpu_percent}%`;
  return `${cpu}, ${process.rss_mb} MB`;
}

function drawPoseModel(result) {
  const ctx = poseModelEl.getContext("2d");
  const width = poseModelEl.width;
  const height = poseModelEl.height;
  const person = result && result.persons && result.persons.length ? result.persons[0] : null;
  const keypoints = person ? person.keypoints : [];

  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#080a0c";
  ctx.fillRect(0, 0, width, height);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.lineWidth = 4;

  for (const [start, end] of POSE_CONNECTIONS) {
    const active = pointConfidence(keypoints, start) >= 0.25 && pointConfidence(keypoints, end) >= 0.25;
    ctx.strokeStyle = active ? "#e9c46a" : "#3a444d";
    ctx.beginPath();
    ctx.moveTo(STANDARD_POSE[start][0], STANDARD_POSE[start][1]);
    ctx.lineTo(STANDARD_POSE[end][0], STANDARD_POSE[end][1]);
    ctx.stroke();
  }

  for (let index = 0; index < STANDARD_POSE.length; index += 1) {
    const confidence = pointConfidence(keypoints, index);
    const active = confidence >= 0.25;
    const face = index <= 4;
    const [x, y] = STANDARD_POSE[index];
    ctx.fillStyle = active ? (face ? "#5ec8a5" : "#e9c46a") : "#46525c";
    ctx.beginPath();
    ctx.arc(x, y, active ? 7 : 6, 0, Math.PI * 2);
    ctx.fill();
    drawConfidenceLabel(ctx, index, confidence, active, face);
  }
}

function renderFaceCover(result) {
  const person = result && result.persons && result.persons.length ? result.persons[0] : null;
  if (!person || !person.keypoints) {
    setCoverPanel("caution", "No person", "No pose result yet", {});
    return false;
  }

  // YOLOv8-pose face keypoints:
  // 0 = nose, 1 = left eye, 2 = right eye, 3 = left ear, 4 = right ear.
  // A keypoint is treated as visible when confidence >= FACE_POINT_THRESHOLD.
  // Tune FACE_POINT_THRESHOLD or the branch rules below if later tests show
  // the camera angle, bedding, or lighting makes the current rule too strict.
  const face = {
    nose: facePoint(person.keypoints, 0),
    leftEye: facePoint(person.keypoints, 1),
    rightEye: facePoint(person.keypoints, 2),
    leftEar: facePoint(person.keypoints, 3),
    rightEar: facePoint(person.keypoints, 4),
  };

  // Front-face evidence: nose or either eye is visible.
  // Ear-only evidence: no nose/eye is visible, but at least one ear is visible.
  // Current rule:
  // - frontVisible => normal face-visible state.
  // - !frontVisible && earVisible => suspected covered face / rollover.
  // - !frontVisible && !earVisible => suspected covered face / rollover.
  // If you later want fewer false alarms, this is the main decision block to adjust.
  const frontVisible = face.nose.present || face.leftEye.present || face.rightEye.present;
  const earVisible = face.leftEar.present || face.rightEar.present;
  const visibleNames = Object.entries(face).filter(([, point]) => point.present).map(([name]) => name);

  let mode = "clear";
  let status = "Face visible";
  let reason = "Nose or eye point is visible";
  if (!frontVisible && earVisible) {
    mode = "covered";
    status = "Suspected face cover / rollover";
    reason = "Nose and eyes are missing; only ear point is visible";
  } else if (!frontVisible && !earVisible) {
    mode = "covered";
    status = "Suspected face cover / rollover";
    reason = "No face keypoint is visible";
  }

  setCoverPanel(mode, status, reason, face, visibleNames);
  return mode === "covered";
}

const FACE_POINT_THRESHOLD = 0.25;

function facePoint(keypoints, index) {
  const point = keypoints[index] || [0, 0, 0];
  const confidence = Number(point[2] || 0);
  return { confidence, present: confidence >= FACE_POINT_THRESHOLD };
}

function setCoverPanel(mode, status, reason, face, visibleNames = []) {
  coverPanelEl.classList.remove("covered", "clear", "caution");
  coverPanelEl.classList.add(mode);
  coverFields.status.textContent = status;
  coverFields.reason.textContent = reason;
  coverFields.visiblePoints.textContent = visibleNames.length ? visibleNames.join(", ") : "none";
  coverFields.nose.textContent = formatFacePoint(face.nose);
  coverFields.leftEye.textContent = formatFacePoint(face.leftEye);
  coverFields.rightEye.textContent = formatFacePoint(face.rightEye);
  coverFields.leftEar.textContent = formatFacePoint(face.leftEar);
  coverFields.rightEar.textContent = formatFacePoint(face.rightEar);
}

function formatFacePoint(point) {
  if (!point) return "-";
  return `${point.present ? "visible" : "missing"} (${point.confidence.toFixed(2)})`;
}

function setCryPanel(mode, status) {
  cryPanelEl.classList.remove("covered", "clear", "caution");
  cryPanelEl.classList.add(mode);
  yamnetFields.status.textContent = status;
}

function renderYamnet(result) {
  if (!result) {
    cryingAwake = false;
    cryLoopStateEl.textContent = "waiting";
    setCryPanel("caution", "Waiting for sample");
    renderNoise(null);
    return;
  }
  if (result.status === "recording") {
    cryingAwake = false;
    cryLoopStateEl.textContent = "active";
    setCryPanel("caution", "Recording...");
    renderNoise(null, "recording");
    return;
  }
  if (result.status === "warming") {
    cryingAwake = false;
    cryLoopStateEl.textContent = "active";
    setCryPanel("caution", "Buffering audio...");
    yamnetFields.cryScore.textContent = result.cry_score == null ? "-" : String(result.cry_score);
    renderNoise(null, "warming");
    return;
  }
  if (result.status === "error") {
    cryingAwake = false;
    cryLoopStateEl.textContent = "error";
    setCryPanel("caution", result.error || "Audio detection error");
    renderNoise(null, "unavailable");
    return;
  }
  cryingAwake = result.crying === true;
  cryLoopStateEl.textContent = "active";
  setCryPanel(result.crying ? "covered" : "clear", result.crying ? "Crying suspected" : "No crying detected");
  if (result.cry_score == null) {
    yamnetFields.cryScore.textContent = "-";
  } else {
    yamnetFields.cryScore.textContent = String(result.cry_score);
  }
  yamnetFields.latency.textContent = result.elapsed_ms == null ? "-" : `${result.elapsed_ms} ms`;
  yamnetFields.top.textContent = (result.top || [])
    .slice(0, 5)
    .map((item) => `${item.label} ${item.score}`)
    .join(", ");
  renderNoise(result);
}

function renderNoise(result, fallback = "waiting") {
  if (!yamnetFields.noise) return;
  if (!result) {
    yamnetFields.noise.textContent = fallback;
    return;
  }
  const noiseDb = Number(result.noise_db);
  if (Number.isFinite(noiseDb)) {
    yamnetFields.noise.textContent = `${noiseDb.toFixed(1)} dB`;
    return;
  }
  const dbfs = Number(result.dbfs);
  yamnetFields.noise.textContent = Number.isFinite(dbfs) ? `${Math.max(0, Math.min(120, dbfs + 94)).toFixed(1)} dB` : "-";
}

function renderAudioPlayback(playback, config) {
  if (!audioPlaybackStateEl || !audioPlaybackStatusEl || !audioPlaybackLinkEl) {
    return;
  }
  if (!playback) {
    audioPlaybackStateEl.textContent = "idle";
    audioPlaybackStatusEl.textContent = "waiting";
    audioPlaybackLinkEl.style.display = "none";
    return;
  }
  const result = playback.last_result || {};
  const capture = playback.capture || {};
  if (!playbackPending && !playbackValueEditing && config && config.audio_playback_volume != null) {
    syncPlaybackVolume(config.audio_playback_volume);
  }
  const captureRunning = Boolean(playback.capture_running);
  captureButtonEl.textContent = captureRunning ? "Stop" : "Start";
  captureButtonEl.disabled = playbackPending || playback.running;
  playbackButtonEl.disabled = playback.running || playbackPending || captureRunning || !capture.file;
  audioPlaybackStateEl.textContent = captureRunning ? "capturing" : (playback.running ? "playing" : (result.status || capture.status || "idle"));
  if (playback.error) {
    audioPlaybackStatusEl.textContent = playback.error;
  } else if (captureRunning) {
    audioPlaybackStatusEl.textContent = `capturing ${formatSeconds(playback.capture_elapsed_s || 0)}`;
  } else if (playback.running) {
    audioPlaybackStatusEl.textContent = `playing ${result.seconds || "-"}s on USB audio`;
  } else if (result.status === "ok") {
    audioPlaybackStatusEl.textContent = `played in ${formatMs(result.elapsed_ms)} on USB audio`;
  } else if (result.status === "playing") {
    audioPlaybackStatusEl.textContent = "starting USB audio";
  } else if (capture.file) {
    audioPlaybackStatusEl.textContent = `sample ready / ${formatSeconds(capture.seconds)}`;
  } else {
    audioPlaybackStatusEl.textContent = "idle";
  }
  if (playbackDeviceEl) {
    playbackDeviceEl.textContent = "USB audio";
  }
  const audioUrl = result.audio_url || capture.audio_url;
  if (audioUrl) {
    audioPlaybackLinkEl.href = audioUrl;
    audioPlaybackLinkEl.textContent = audioUrl;
    audioPlaybackLinkEl.style.display = "inline-block";
  } else {
    audioPlaybackLinkEl.style.display = "none";
  }
}

async function requestAudioPlayback() {
  const volume = clampNumber(playbackVolumeEl.value, 0, 2, 1);
  syncPlaybackVolume(volume);
  playbackPending = true;
  playbackButtonEl.disabled = true;
  audioPlaybackStateEl.textContent = "starting";
  audioPlaybackStatusEl.textContent = "sending playback request";
  try {
    const payload = await api("/api/audio/playback/play", {
      method: "POST",
      body: JSON.stringify({
        volume,
      }),
    });
    renderAudioPlayback({ running: true, last_result: payload.result }, null);
  } catch (err) {
    audioPlaybackStateEl.textContent = "error";
    audioPlaybackStatusEl.textContent = err.message;
  } finally {
    playbackPending = false;
    refresh();
  }
}

function syncPlaybackVolume(value) {
  const volume = clampNumber(value, 0, 2, 1);
  const text = volume.toFixed(1);
  playbackVolumeEl.value = text;
  playbackVolumeRangeEl.value = text;
  if (playbackVolumeValueEl) playbackVolumeValueEl.textContent = `${text}x`;
  return volume;
}

async function togglePlaybackCapture() {
  const stopping = captureButtonEl.textContent === "Stop";
  const volume = clampNumber(playbackVolumeEl.value, 0, 2, 1);
  playbackPending = true;
  captureButtonEl.disabled = true;
  playbackButtonEl.disabled = true;
  audioPlaybackStateEl.textContent = stopping ? "saving" : "capturing";
  audioPlaybackStatusEl.textContent = stopping ? "saving sample" : "capture started";
  try {
    const payload = await api(stopping ? "/api/audio/playback/capture/stop" : "/api/audio/playback/capture/start", {
      method: "POST",
      body: JSON.stringify({ volume }),
    });
    if (stopping) {
      renderAudioPlayback({ running: false, capture: payload.result, last_result: null }, null);
    } else {
      renderAudioPlayback({ running: false, capture_running: true, capture_started_at: payload.result.capture_started_at }, null);
    }
  } catch (err) {
    audioPlaybackStateEl.textContent = "error";
    audioPlaybackStatusEl.textContent = err.message;
  } finally {
    playbackPending = false;
    captureButtonEl.disabled = false;
    refresh();
  }
}

function drawConfidenceLabel(ctx, index, confidence, active, face) {
  const [x, y] = STANDARD_POSE[index];
  const [dx, dy] = CONFIDENCE_LABEL_OFFSETS[index];
  const text = confidence.toFixed(2);
  const labelX = x + dx;
  const labelY = y + dy;
  ctx.font = "11px system-ui, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  const boxWidth = ctx.measureText(text).width + 8;
  const boxHeight = 16;
  ctx.fillStyle = active ? "rgba(32, 38, 42, 0.94)" : "rgba(18, 22, 25, 0.9)";
  roundRect(ctx, labelX - 4, labelY - boxHeight / 2, boxWidth, boxHeight, 4);
  ctx.fill();
  ctx.fillStyle = active ? (face ? "#5ec8a5" : "#e9c46a") : "#88939d";
  ctx.fillText(text, labelX, labelY + 0.5);
}

function roundRect(ctx, x, y, width, height, radius) {
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.lineTo(x + width - radius, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
  ctx.lineTo(x + width, y + height - radius);
  ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
  ctx.lineTo(x + radius, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
  ctx.lineTo(x, y + radius);
  ctx.quadraticCurveTo(x, y, x + radius, y);
  ctx.closePath();
}

function pointConfidence(keypoints, index) {
  const point = keypoints[index];
  return point ? Number(point[2] || 0) : 0;
}

function drawOverlay() {
  const ctx = overlayEl.getContext("2d");
  const rect = overlayEl.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  overlayEl.width = Math.max(1, Math.round(rect.width * dpr));
  overlayEl.height = Math.max(1, Math.round(rect.height * dpr));
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, rect.width, rect.height);
  if (!ENABLE_HD_POSE_OVERLAY) return;
  const result = latestDetections;
  if (!result || !result.frame_size || !result.persons) return;
  const [sourceW, sourceH] = result.frame_size;
  const box = containBox(rect.width, rect.height, 16 / 9);
  const scaleX = box.w / sourceW;
  const scaleY = box.h / sourceH;
  ctx.lineWidth = 2;
  ctx.strokeStyle = "#e9c46a";
  ctx.fillStyle = "#e9c46a";
  for (const person of result.persons) {
    const [x1, y1, x2, y2] = person.box;
    ctx.strokeRect(box.x + x1 * scaleX, box.y + y1 * scaleY, (x2 - x1) * scaleX, (y2 - y1) * scaleY);
    for (const [start, end] of POSE_CONNECTIONS) {
      const a = person.keypoints[start];
      const b = person.keypoints[end];
      if (!a || !b || a[2] < 0.25 || b[2] < 0.25) continue;
      ctx.beginPath();
      ctx.moveTo(box.x + a[0] * scaleX, box.y + a[1] * scaleY);
      ctx.lineTo(box.x + b[0] * scaleX, box.y + b[1] * scaleY);
      ctx.stroke();
    }
    for (const point of person.keypoints) {
      if (point[2] < 0.25) continue;
      ctx.beginPath();
      ctx.arc(box.x + point[0] * scaleX, box.y + point[1] * scaleY, 3, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

function drawZoneOverlay() {
  const ctx = zoneOverlayEl.getContext("2d");
  const rect = zoneOverlayEl.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  zoneOverlayEl.width = Math.max(1, Math.round(rect.width * dpr));
  zoneOverlayEl.height = Math.max(1, Math.round(rect.height * dpr));
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, rect.width, rect.height);
  const contentBox = lowresContentBox(rect.width, rect.height);
  drawPersonBox(ctx, contentBox);
  drawActivityZone(ctx, contentBox);
}

function drawPersonBox(ctx, contentBox) {
  const box = personBox(latestDetections);
  if (!box) return;
  const x = contentBox.x + box.left * contentBox.w;
  const y = contentBox.y + box.top * contentBox.h;
  const w = Math.max(1, (box.right - box.left) * contentBox.w);
  const h = Math.max(1, (box.bottom - box.top) * contentBox.h);
  ctx.strokeStyle = "#1f5f4f";
  ctx.lineWidth = 3;
  ctx.strokeRect(x, y, w, h);
}

function drawActivityZone(ctx, contentBox) {
  const zone = activityZone.zone;
  if (!zone) return;
  const flashOn = activityZone.triggered && Math.floor(Date.now() / 260) % 2 === 0;
  const color = "#5ec8a5";
  ctx.globalAlpha = flashOn ? 1 : 0.62;
  ctx.strokeStyle = color;
  ctx.lineWidth = flashOn ? 5 : 3;
  ctx.beginPath();
  zone.points.forEach((point, index) => {
    const x = contentBox.x + point.x * contentBox.w;
    const y = contentBox.y + point.y * contentBox.h;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.closePath();
  ctx.stroke();
  ctx.globalAlpha = 1;
}

function lowresContentBox(width, height) {
  const frameSize = latestDetections && Array.isArray(latestDetections.frame_size) ? latestDetections.frame_size : null;
  if (!frameSize || frameSize.length < 2) return { x: 0, y: 0, w: width, h: height };
  const frameW = Number(frameSize[0]);
  const frameH = Number(frameSize[1]);
  if (!Number.isFinite(frameW) || !Number.isFinite(frameH) || frameW <= 0 || frameH <= 0) {
    return { x: 0, y: 0, w: width, h: height };
  }
  return containBox(width, height, frameW / frameH);
}

function personBox(result) {
  const person = result && result.persons && result.persons.length ? result.persons[0] : null;
  const box = person && Array.isArray(person.box) ? person.box : null;
  const frameSize = result && Array.isArray(result.frame_size) ? result.frame_size : null;
  if (!box || box.length < 4 || !frameSize || frameSize.length < 2) return null;
  const frameW = Number(frameSize[0]);
  const frameH = Number(frameSize[1]);
  if (!Number.isFinite(frameW) || !Number.isFinite(frameH) || frameW <= 0 || frameH <= 0) return null;
  return normalizeZone({
    left: Number(box[0]) / frameW,
    top: Number(box[1]) / frameH,
    right: Number(box[2]) / frameW,
    bottom: Number(box[3]) / frameH,
  });
}

function containBox(w, h, ratio) {
  const current = w / h;
  if (current > ratio) {
    const boxW = h * ratio;
    return { x: (w - boxW) / 2, y: 0, w: boxW, h };
  }
  const boxH = w / ratio;
  return { x: 0, y: (h - boxH) / 2, w, h: boxH };
}

async function refresh() {
  try {
    const status = await api("/api/status");
    renderRuntime(status);
    renderResources(status.resources);
    if (status.camera.error) {
      setStatus(status.camera.error);
    } else if (status.camera.preview_ready) {
      setStatus("Live preview ready");
    }
    const detections = await api("/api/detections");
    latestDetections = detections.result;
    drawPoseModel(latestDetections);
    faceCoverAwake = renderFaceCover(latestDetections);
    updateSleepState(latestDetections);
    // HD overlay is intentionally disabled for now. drawOverlay() still clears
    // the canvas and keeps the old drawing path ready for later restoration.
    drawOverlay();
    drawZoneOverlay();
  } catch (err) {
    setStatus(err.message);
  }
}

async function refreshSnapshot() {
  if (snapshotLoading) return;
  if (document.hidden) {
    scheduleSnapshotRefresh(250);
    return;
  }
  snapshotLoading = true;
  snapshotRequestedAt = performance.now();
  try {
    await replaceWithLatestFrame(snapshotEl, "/api/snapshot");
    scheduleSnapshotRefresh();
  } catch (_) {
    scheduleSnapshotRefresh(100);
  } finally {
    snapshotLoading = false;
  }
}

function scheduleSnapshotRefresh(delay = null) {
  if (snapshotTimer) {
    window.clearTimeout(snapshotTimer);
  }
  const elapsed = snapshotRequestedAt > 0 ? performance.now() - snapshotRequestedAt : 0;
  const nextDelay = delay == null ? Math.max(0, snapshotRefreshMs - elapsed) : delay;
  snapshotTimer = window.setTimeout(refreshSnapshot, nextDelay);
}

window.addEventListener("resize", () => {
  drawOverlay();
  drawZoneOverlay();
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    refreshPreview();
  }
});
lightSliderEl.addEventListener("input", () => queueLightDuty(lightSliderEl.value));
lightDutyInputEl.addEventListener("change", () => queueLightDuty(lightDutyInputEl.value));
for (const button of document.querySelectorAll("[data-light-duty]")) {
  button.addEventListener("click", () => queueLightDuty(button.dataset.lightDuty));
}
playbackButtonEl.addEventListener("click", requestAudioPlayback);
captureButtonEl.addEventListener("click", togglePlaybackCapture);
playbackVolumeRangeEl.addEventListener("input", () => {
  playbackValueEditing = true;
  syncPlaybackVolume(playbackVolumeRangeEl.value);
});
playbackVolumeEl.addEventListener("input", () => {
  playbackValueEditing = true;
  syncPlaybackVolume(playbackVolumeEl.value);
});
syncPlaybackVolume(playbackVolumeEl.value);
refreshPreview();
drawPoseModel(null);
faceCoverAwake = renderFaceCover(null);
updateSleepState(null);
drawZoneOverlay();
zoneBlinkTimer = window.setInterval(drawZoneOverlay, 260);
refresh();
refreshSnapshot();
if (voiceToggleEl) voiceToggleEl.addEventListener("click", toggleVoiceTrigger);
setInterval(refresh, 1000);
refreshCare();
setInterval(refreshCare, 5000);
