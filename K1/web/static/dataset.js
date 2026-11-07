const statusEl = document.getElementById("status");
const liveMetaEl = document.getElementById("liveMeta");
const pendingMetaEl = document.getElementById("pendingMeta");
const liveSnapshotEl = document.getElementById("liveSnapshot");
const pendingRawEl = document.getElementById("pendingRaw");
const lightInputs = Array.from(document.querySelectorAll("input[name='lightLabel']"));
const captureBtn = document.getElementById("captureBtn");
const confirmBtn = document.getElementById("confirmBtn");
const cancelBtn = document.getElementById("cancelBtn");
const datasetRootEl = document.getElementById("datasetRoot");
const rawCountEl = document.getElementById("rawCount");
const metaCountEl = document.getElementById("metaCount");
const pendingCountEl = document.getElementById("pendingCount");
const lastSampleEl = document.getElementById("lastSample");

let pendingId = null;
let snapshotTimer = null;
let lightTouched = false;

function setStatus(text) {
  statusEl.textContent = text;
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
  return payload.result;
}

function refreshSnapshot() {
  liveSnapshotEl.src = `/api/raw-snapshot?t=${Date.now()}`;
}

function scheduleSnapshot(delay = 500) {
  if (snapshotTimer) window.clearTimeout(snapshotTimer);
  snapshotTimer = window.setTimeout(refreshSnapshot, delay);
}

function renderStats(stats) {
  const counts = stats.counts || {};
  datasetRootEl.textContent = stats.root || "-";
  rawCountEl.textContent = String(counts.raw ?? 0);
  metaCountEl.textContent = String(counts.meta ?? 0);
  pendingCountEl.textContent = String(counts.pending ?? 0);
  if (!lightTouched) {
    setLightLabel(stats.day_night && stats.day_night.state === "night" ? "night_ir" : "day");
  }
}

function setPending(sample) {
  pendingId = sample ? sample.id : null;
  confirmBtn.disabled = !pendingId;
  cancelBtn.disabled = !pendingId;
  if (!sample) {
    pendingRawEl.removeAttribute("src");
    pendingMetaEl.textContent = "no pending sample";
    return;
  }
  pendingRawEl.src = `${sample.raw_url}?t=${Date.now()}`;
  const light = sample.labels && sample.labels.light ? sample.labels.light : currentLightLabel();
  pendingMetaEl.textContent = `${sample.id} · ${light}`;
  lastSampleEl.textContent = sample.id;
  renderStats({ root: datasetRootEl.textContent, counts: sample.counts || {} });
}

async function refreshStats() {
  try {
    const stats = await api("/api/dataset/stats");
    renderStats(stats);
    setStatus("stats updated");
  } catch (err) {
    setStatus(err.message);
  }
}

async function captureSample() {
  captureBtn.disabled = true;
  setStatus("capturing");
  try {
    if (pendingId) {
      await api("/api/dataset/cancel", {
        method: "POST",
        body: JSON.stringify({ id: pendingId }),
      });
    }
    const sample = await api("/api/dataset/capture", {
      method: "POST",
      body: JSON.stringify({ labels: { light: currentLightLabel() } }),
    });
    setPending(sample);
    setStatus("review pending sample");
  } catch (err) {
    setStatus(err.message);
  } finally {
    captureBtn.disabled = false;
  }
}

async function confirmSample() {
  if (!pendingId) return;
  confirmBtn.disabled = true;
  cancelBtn.disabled = true;
  setStatus("confirming");
  try {
    const result = await api("/api/dataset/confirm", {
      method: "POST",
      body: JSON.stringify({ id: pendingId }),
    });
    lastSampleEl.textContent = `${result.id} confirmed`;
    renderStats({ root: datasetRootEl.textContent, counts: result.counts || {} });
    setPending(null);
    setStatus("sample saved");
  } catch (err) {
    setStatus(err.message);
    confirmBtn.disabled = false;
    cancelBtn.disabled = false;
  }
}

async function cancelSample() {
  if (!pendingId) return;
  confirmBtn.disabled = true;
  cancelBtn.disabled = true;
  setStatus("canceling");
  try {
    const result = await api("/api/dataset/cancel", {
      method: "POST",
      body: JSON.stringify({ id: pendingId }),
    });
    renderStats({ root: datasetRootEl.textContent, counts: result.counts || {} });
    lastSampleEl.textContent = `${pendingId} canceled`;
    setPending(null);
    setStatus("pending sample discarded");
  } catch (err) {
    setStatus(err.message);
    confirmBtn.disabled = false;
    cancelBtn.disabled = false;
  }
}

liveSnapshotEl.addEventListener("load", () => {
  liveMetaEl.textContent = `${liveSnapshotEl.naturalWidth}x${liveSnapshotEl.naturalHeight}`;
  scheduleSnapshot(500);
});
liveSnapshotEl.addEventListener("error", () => scheduleSnapshot(1000));
captureBtn.addEventListener("click", captureSample);
confirmBtn.addEventListener("click", confirmSample);
cancelBtn.addEventListener("click", cancelSample);
for (const input of lightInputs) {
  input.addEventListener("change", () => {
    lightTouched = true;
  });
}

refreshStats();
refreshSnapshot();
setInterval(refreshStats, 5000);

function currentLightLabel() {
  const selected = lightInputs.find((input) => input.checked);
  return selected ? selected.value : "day";
}

function setLightLabel(value) {
  for (const input of lightInputs) {
    input.checked = input.value === value;
  }
}
