"""Baby care records (feeding / diaper / sleep / growth) stored on the board.

Backs the naviga app's Care page (``/api/care/records``, ``/api/care/overview``,
``/api/care/profile``) and the voice worker, which turns phrases such as
"换尿布" / "喂奶" into records through the same HTTP API.

Wire format (what the app parses):
    record  = {id, type, start_at, end_at?, kind?, side?, duration_min?, amount_ml?,
               milk?, food?, weight_kg?, height_cm?, head_cm?, note?, source?}
    times are epoch seconds (float); the app multiplies by 1000.
Errors are raised as CareError; the English message fragments ("already in
progress", "in the future", "must be between", ...) are matched by the app to
show its own Chinese text, so keep them stable.
"""
from __future__ import annotations

import datetime as dt
import secrets
import statistics
import threading
import time
from pathlib import Path
from typing import Any, Callable

from k1edge.runtime_store import read_json, write_json

RECORD_TYPES = ("feeding", "diaper", "sleep", "growth")
FEEDING_KINDS = ("breast", "bottle", "solid")
BREAST_SIDES = ("left", "right", "both")
MILK_TYPES = ("breast_milk", "formula")
DIAPER_KINDS = ("wet", "dirty", "mixed", "dry")
SEXES = ("female", "male")
SOURCES = ("app", "voice", "web", "api")

MAX_RECORDS = 20000
FUTURE_SKEW_S = 300.0
MAX_SLEEP_S = 24 * 3600.0
NUMERIC_LIMITS = {
    "duration_min": (0, 240),
    "amount_ml": (0, 500),
    "weight_kg": (0.5, 40.0),
    "height_cm": (30.0, 150.0),
    "head_cm": (20.0, 70.0),
}
# Typical awake windows (minutes) by age in months: (max_month_inclusive, low, high).
WAKE_WINDOWS = (
    (0, 35, 60),
    (1, 45, 75),
    (2, 60, 90),
    (3, 75, 105),
    (4, 90, 135),
    (6, 120, 180),
    (8, 150, 210),
    (11, 180, 240),
    (14, 180, 270),
    (18, 240, 330),
    (24, 300, 360),
    (10_000, 300, 390),
)
HISTORY_SAMPLES_FOR_FULL_TRUST = 5


class CareError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class CareNotFound(CareError):
    def __init__(self, record_id: str) -> None:
        super().__init__(f"record not found: {record_id}", status=404)


# --------------------------------------------------------------------------- parsing helpers


def to_seconds(value: Any, field: str) -> float:
    """Accept epoch seconds or milliseconds (numbers or numeric strings)."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise CareError(f"{field} must be a unix timestamp in seconds") from None
    if seconds != seconds or seconds <= 0:
        raise CareError(f"{field} must be a unix timestamp in seconds")
    if seconds > 1e11:  # milliseconds
        seconds /= 1000.0
    return round(seconds, 3)


def _optional_choice(payload: dict[str, Any], key: str, choices: tuple[str, ...]) -> str | None:
    value = payload.get(key)
    if value in (None, ""):
        return None
    value = str(value).strip().lower()
    if value not in choices:
        raise CareError(f"{key} must be one of {', '.join(choices)}")
    return value


def _optional_number(payload: dict[str, Any], key: str, integer: bool = False) -> float | int | None:
    value = payload.get(key)
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise CareError(f"{key} must be a number") from None
    low, high = NUMERIC_LIMITS[key]
    if not low <= number <= high:
        raise CareError(f"{key} must be between {low:g} and {high:g}")
    return int(round(number)) if integer else round(number, 2)


def _optional_text(payload: dict[str, Any], key: str, limit: int) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


def new_record_id(now: float) -> str:
    return f"{int(now * 1000):x}{secrets.token_hex(3)}"


def local_date(ts: float) -> dt.date:
    return dt.datetime.fromtimestamp(ts).date()


def local_midnight(ts: float) -> float:
    day = dt.datetime.fromtimestamp(ts)
    return day.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def age_parts(birth: dt.date, today: dt.date) -> dict[str, int]:
    days = (today - birth).days
    months = (today.year - birth.year) * 12 + today.month - birth.month
    if today.day < birth.day:
        months -= 1
    months = max(0, months)
    anchor_year = birth.year + (birth.month - 1 + months) // 12
    anchor_month = (birth.month - 1 + months) % 12 + 1
    anchor_day = min(birth.day, _days_in_month(anchor_year, anchor_month))
    month_days = (today - dt.date(anchor_year, anchor_month, anchor_day)).days
    return {"days": days, "months": months, "month_days": max(0, month_days)}


def _days_in_month(year: int, month: int) -> int:
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    return (nxt - dt.timedelta(days=1)).day


def wake_window_for_months(months: int) -> tuple[int, int]:
    for max_month, low, high in WAKE_WINDOWS:
        if months <= max_month:
            return low, high
    return WAKE_WINDOWS[-1][1], WAKE_WINDOWS[-1][2]


# --------------------------------------------------------------------------- store


class CareStore:
    def __init__(
        self,
        records_path: Path,
        profile_path: Path,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.records_path = Path(records_path)
        self.profile_path = Path(profile_path)
        self.clock = clock
        self._lock = threading.RLock()
        self._records: list[dict[str, Any]] | None = None

    # ---------------------------------------------------------------- persistence

    def _load(self) -> list[dict[str, Any]]:
        if self._records is None:
            payload = read_json(self.records_path)
            records = payload.get("records")
            self._records = [item for item in records if isinstance(item, dict) and item.get("id")] if isinstance(records, list) else []
        return self._records

    def _save(self) -> None:
        records = self._load()
        if len(records) > MAX_RECORDS:
            records.sort(key=lambda item: float(item.get("start_at") or 0.0))
            del records[: len(records) - MAX_RECORDS]
        write_json(self.records_path, {"records": records, "updated_at": self.clock()})

    # ---------------------------------------------------------------- records

    def list_records(
        self,
        since: Any = None,
        record_type: str | None = None,
        limit: Any = None,
        until: Any = None,
    ) -> list[dict[str, Any]]:
        since_s = to_seconds(since, "since") if since not in (None, "") else None
        until_s = to_seconds(until, "until") if until not in (None, "") else None
        if record_type not in (None, ""):
            record_type = str(record_type).strip().lower()
            if record_type not in RECORD_TYPES:
                raise CareError(f"type must be one of {', '.join(RECORD_TYPES)}")
        try:
            max_items = int(limit) if limit not in (None, "") else 500
        except (TypeError, ValueError):
            raise CareError("limit must be an integer") from None
        max_items = max(1, min(max_items, 5000))
        with self._lock:
            items = []
            for record in self._load():
                if record_type and record.get("type") != record_type:
                    continue
                start = float(record.get("start_at") or 0.0)
                end = float(record.get("end_at") or start)
                # A sleep that started before `since` but is still running/ended after it is still relevant.
                if since_s is not None and max(start, end) < since_s and not (
                    record.get("type") == "sleep" and record.get("end_at") is None
                ):
                    continue
                if until_s is not None and start > until_s:
                    continue
                items.append(dict(record))
        items.sort(key=lambda item: float(item.get("start_at") or 0.0), reverse=True)
        return items[:max_items]

    def get_record(self, record_id: str) -> dict[str, Any]:
        with self._lock:
            for record in self._load():
                if record.get("id") == record_id:
                    return dict(record)
        raise CareNotFound(record_id)

    def create_record(self, payload: dict[str, Any], source: str | None = None) -> dict[str, Any]:
        with self._lock:
            now = self.clock()
            record = self._normalize(payload, now=now)
            record_id = str(payload.get("id") or "").strip()
            if record_id and any(item.get("id") == record_id for item in self._load()):
                return self._update_locked(record_id, payload, now=now)
            if record["type"] == "sleep" and record.get("end_at") is None and self._open_sleep_locked() is not None:
                raise CareError("a sleep is already in progress")
            record["id"] = record_id[:64] or new_record_id(now)
            record["source"] = _source(payload.get("source") or source)
            record["created_at"] = now
            record["updated_at"] = now
            self._load().append(record)
            self._save()
            return dict(record)

    def update_record(self, record_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return self._update_locked(record_id, payload, now=self.clock())

    def _update_locked(self, record_id: str, payload: dict[str, Any], now: float) -> dict[str, Any]:
        records = self._load()
        index = next((i for i, item in enumerate(records) if item.get("id") == record_id), None)
        if index is None:
            raise CareNotFound(record_id)
        old = records[index]
        merged = dict(payload)
        merged.setdefault("type", old.get("type"))
        merged.setdefault("start_at", old.get("start_at"))
        if old.get("type") == "sleep" and old.get("end_at") is not None and merged.get("end_at") is None:
            if "end_at" not in payload:
                merged["end_at"] = old.get("end_at")
        record = self._normalize(merged, now=now)
        if record["type"] == "sleep" and record.get("end_at") is None:
            other = self._open_sleep_locked()
            if other is not None and other.get("id") != record_id:
                raise CareError("a sleep is already in progress")
        record["id"] = record_id
        record["source"] = old.get("source") or _source(payload.get("source"))
        for key in ("voice_text", "created_at"):
            if old.get(key) is not None:
                record[key] = old[key]
        if payload.get("voice_text"):
            record["voice_text"] = str(payload["voice_text"])[:500]
        record["updated_at"] = now
        records[index] = record
        self._save()
        return dict(record)

    def end_sleep(self, end_at: Any = None, record_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            open_sleep = self.get_record(record_id) if record_id else self._open_sleep_locked()
            if open_sleep is None or open_sleep.get("type") != "sleep" or open_sleep.get("end_at") is not None:
                raise CareError("no sleep in progress")
            payload = dict(open_sleep)
            payload["end_at"] = self.clock() if end_at in (None, "") else end_at
            return self._update_locked(open_sleep["id"], payload, now=self.clock())

    def delete_record(self, record_id: str) -> dict[str, Any]:
        with self._lock:
            records = self._load()
            for index, record in enumerate(records):
                if record.get("id") == record_id:
                    del records[index]
                    self._save()
                    return {"id": record_id, "deleted": True}
        raise CareNotFound(record_id)

    def open_sleep(self) -> dict[str, Any] | None:
        with self._lock:
            record = self._open_sleep_locked()
            return dict(record) if record else None

    def _open_sleep_locked(self) -> dict[str, Any] | None:
        candidates = [item for item in self._load() if item.get("type") == "sleep" and item.get("end_at") is None]
        return max(candidates, key=lambda item: float(item.get("start_at") or 0.0)) if candidates else None

    def _normalize(self, payload: dict[str, Any], now: float) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise CareError("body must be a JSON object")
        record_type = str(payload.get("type") or "").strip().lower()
        if record_type not in RECORD_TYPES:
            raise CareError(f"type must be one of {', '.join(RECORD_TYPES)}")
        start_at = to_seconds(payload.get("start_at") if payload.get("start_at") not in (None, "") else now, "start_at")
        if start_at > now + FUTURE_SKEW_S:
            raise CareError("start_at is in the future")
        record: dict[str, Any] = {"type": record_type, "start_at": start_at}
        note = _optional_text(payload, "note", 200)
        if note:
            record["note"] = note

        if record_type == "feeding":
            kind = _optional_choice(payload, "kind", FEEDING_KINDS)
            side = _optional_choice(payload, "side", BREAST_SIDES)
            milk = _optional_choice(payload, "milk", MILK_TYPES)
            duration = _optional_number(payload, "duration_min", integer=True)
            amount = _optional_number(payload, "amount_ml", integer=True)
            food = _optional_text(payload, "food", 60)
            for key, value in (("kind", kind), ("side", side), ("milk", milk), ("duration_min", duration), ("amount_ml", amount), ("food", food)):
                if value is not None:
                    record[key] = value
        elif record_type == "diaper":
            kind = _optional_choice(payload, "kind", DIAPER_KINDS)
            if kind:
                record["kind"] = kind
        elif record_type == "sleep":
            end_raw = payload.get("end_at")
            if end_raw not in (None, ""):
                end_at = to_seconds(end_raw, "end_at")
                if end_at > now + FUTURE_SKEW_S:
                    raise CareError("end_at is in the future")
                if end_at <= start_at:
                    raise CareError("end_at must be after start_at")
                if end_at - start_at > MAX_SLEEP_S:
                    raise CareError("a sleep cannot be longer than 24 hours")
                record["end_at"] = end_at
            else:
                if now - start_at > MAX_SLEEP_S:
                    raise CareError("a sleep cannot be longer than 24 hours")
                record["end_at"] = None
        elif record_type == "growth":
            values = {key: _optional_number(payload, key) for key in ("weight_kg", "height_cm", "head_cm")}
            if all(value is None for value in values.values()):
                raise CareError("at least one of weight_kg, height_cm, head_cm is required")
            record.update({key: value for key, value in values.items() if value is not None})
        return record

    # ---------------------------------------------------------------- profile

    def profile(self) -> dict[str, Any]:
        payload = read_json(self.profile_path)
        return {
            "name": payload.get("name") or "",
            "birth_date": payload.get("birth_date"),
            "sex": payload.get("sex"),
        }

    def save_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise CareError("body must be a JSON object")
        current = self.profile()
        name = current["name"] if "name" not in payload else (str(payload.get("name") or "").strip()[:20])
        birth_date = current["birth_date"]
        if "birth_date" in payload:
            raw = payload.get("birth_date")
            if raw in (None, ""):
                birth_date = None
            else:
                try:
                    parsed = dt.date.fromisoformat(str(raw)[:10])
                except ValueError:
                    raise CareError("birth_date must be YYYY-MM-DD") from None
                today = local_date(self.clock())
                if parsed > today or (today - parsed).days > 366 * 7:
                    raise CareError("birth_date must be within the last 7 years")
                birth_date = parsed.isoformat()
        sex = current["sex"]
        if "sex" in payload:
            sex = _optional_choice(payload, "sex", SEXES)
        profile = {"name": name, "birth_date": birth_date, "sex": sex}
        with self._lock:
            write_json(self.profile_path, {**profile, "updated_at": self.clock()})
        return profile

    # ---------------------------------------------------------------- overview

    def overview(self, now: float | None = None) -> dict[str, Any]:
        now = self.clock() if now is None else now
        day_start = local_midnight(now)
        with self._lock:
            records = [dict(item) for item in self._load()]
        profile = self.profile()
        birth = None
        if profile.get("birth_date"):
            try:
                birth = dt.date.fromisoformat(profile["birth_date"])
            except ValueError:
                birth = None
        age = age_parts(birth, local_date(now)) if birth else None

        by_type: dict[str, list[dict[str, Any]]] = {name: [] for name in RECORD_TYPES}
        for record in records:
            by_type.setdefault(str(record.get("type")), []).append(record)
        for items in by_type.values():
            items.sort(key=lambda item: float(item.get("start_at") or 0.0), reverse=True)

        today_feedings = [r for r in by_type["feeding"] if day_start <= float(r["start_at"]) <= now + FUTURE_SKEW_S]
        today_diapers = [r for r in by_type["diaper"] if day_start <= float(r["start_at"]) <= now + FUTURE_SKEW_S]
        sleep_minutes = []
        for record in by_type["sleep"]:
            start = float(record["start_at"])
            end = float(record["end_at"]) if record.get("end_at") is not None else now
            overlap = min(end, now) - max(start, day_start)
            if overlap > 0:
                sleep_minutes.append(overlap / 60.0)
        diaper_kinds = [r.get("kind") for r in today_diapers]

        return {
            "now": now,
            "day": {"start_at": day_start},
            "profile": profile if (profile.get("name") or profile.get("birth_date")) else None,
            "age": age,
            "today": {
                "feeding": {
                    "count": len(today_feedings),
                    "bottle_ml": int(sum(int(r.get("amount_ml") or 0) for r in today_feedings if r.get("kind") != "solid")),
                    "breast_min": int(sum(int(r.get("duration_min") or 0) for r in today_feedings if r.get("kind") == "breast")),
                    "solid_count": sum(1 for r in today_feedings if r.get("kind") == "solid"),
                },
                "diaper": {
                    "count": len(today_diapers),
                    "wet": sum(1 for kind in diaper_kinds if kind in {"wet", "mixed"}),
                    "dirty": sum(1 for kind in diaper_kinds if kind in {"dirty", "mixed"}),
                },
                "sleep": {
                    "count": len(sleep_minutes),
                    "total_min": round(sum(sleep_minutes), 1),
                    "longest_min": round(max(sleep_minutes), 1) if sleep_minutes else 0,
                },
            },
            "last_feeding": by_type["feeding"][0] if by_type["feeding"] else None,
            "last_diaper": by_type["diaper"][0] if by_type["diaper"] else None,
            "latest_growth": by_type["growth"][0] if by_type["growth"] else None,
            "nap": self._nap_prediction(by_type["sleep"], age, now),
        }

    def _nap_prediction(self, sleeps: list[dict[str, Any]], age: dict[str, int] | None, now: float) -> dict[str, Any]:
        hour = dt.datetime.fromtimestamp(now).hour
        night = hour >= 19 or hour < 7
        open_sleep = next((s for s in sleeps if s.get("end_at") is None), None)
        if open_sleep is not None:
            return {"state": "sleeping", "sleep_id": open_sleep["id"], "started_at": open_sleep["start_at"], "night": night}
        if age is None:
            return {"state": "need_profile", "night": night}
        low, high = wake_window_for_months(age["months"])
        finished = [s for s in sleeps if s.get("end_at") is not None]
        if not finished:
            return {"state": "need_sleep", "age_range_min": [low, high], "night": night}

        last_wake = max(float(s["end_at"]) for s in finished)
        chronological = sorted(finished, key=lambda s: float(s["start_at"]))
        windows = []
        for prev, nxt in zip(chronological, chronological[1:]):
            prev_end = float(prev["end_at"])
            if now - prev_end > 7 * 86400:
                continue
            wake_hour = dt.datetime.fromtimestamp(prev_end).hour
            gap_min = (float(nxt["start_at"]) - prev_end) / 60.0
            if 7 <= wake_hour < 19 and 15 <= gap_min <= 8 * 60:
                windows.append(gap_min)
        samples = len(windows)
        age_target = (low + high) / 2.0
        if samples == 0:
            basis, target = "age", age_target
        else:
            history = statistics.median(windows)
            history = min(max(history, low * 0.7), high * 1.3)
            if samples >= HISTORY_SAMPLES_FOR_FULL_TRUST:
                basis, target = "history", history
            else:
                weight = samples / HISTORY_SAMPLES_FOR_FULL_TRUST
                basis, target = "blend", age_target * (1 - weight) + history * weight
        spread = max(15.0, (high - low) / 2.0)
        ideal_at = last_wake + target * 60.0
        earliest_at = ideal_at - spread * 60.0
        latest_at = ideal_at + spread * 60.0
        timing = "early" if now < earliest_at else "due" if now <= latest_at else "overdue"
        return {
            "state": "awake",
            "age_range_min": [low, high],
            "night": night,
            "last_wake_at": last_wake,
            "timing": timing,
            "basis": basis,
            "samples": samples,
            "earliest_at": round(earliest_at, 3),
            "ideal_at": round(ideal_at, 3),
            "latest_at": round(latest_at, 3),
        }


def _source(value: Any) -> str:
    text = str(value or "app").strip().lower()
    return text if text in SOURCES else "api"
