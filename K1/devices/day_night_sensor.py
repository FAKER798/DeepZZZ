import time
from typing import Any

from k1edge.runtime_store import read_json
from utils.paths import DAY_NIGHT_LOG, DAY_NIGHT_STATE_FILE, IRCUT_MODE_FILE


def day_night_status() -> dict[str, Any]:
    status: dict[str, Any] = {
        "state": "unknown",
        "source": "unavailable",
        "updated_at": time.time(),
    }
    try:
        payload = read_json(DAY_NIGHT_STATE_FILE)
        value = str(payload.get("state") or DAY_NIGHT_STATE_FILE.read_text(encoding="utf-8").strip())
        if value in {"day", "night"}:
            status.update(
                {
                    "state": value,
                    "source": "day_night_state_file",
                    "updated_at": payload.get("updated_at", status["updated_at"]),
                }
            )
            return status
    except OSError as exc:
        status["error"] = str(exc)

    try:
        for line in reversed(DAY_NIGHT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()):
            value = line.strip()
            if value in {"day", "night"}:
                status.update({"state": value, "source": "day_night_sync_log"})
                return status
    except OSError as exc:
        status["error"] = str(exc)

    try:
        payload = read_json(IRCUT_MODE_FILE)
        value = str(payload.get("mode") or IRCUT_MODE_FILE.read_text(encoding="utf-8").strip())
        if value in {"day", "night", "off"}:
            status.update(
                {
                    "state": value,
                    "source": "ircut_saved_mode",
                    "updated_at": payload.get("updated_at", status["updated_at"]),
                }
            )
            return status
    except OSError as exc:
        status["error"] = str(exc)
    return status
