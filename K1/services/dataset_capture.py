from __future__ import annotations

from datetime import datetime
import json
import shutil
from pathlib import Path
from typing import Any

from utils.paths import DATASET_DIR


class DatasetCaptureService:
    def __init__(self, enabled: bool, root: Path = DATASET_DIR) -> None:
        self.enabled = enabled
        self.root = root
        self.raw_dir = root / "raw"
        self.meta_dir = root / "meta"
        self.pending_dir = root / "pending"
        self.pending_raw_dir = self.pending_dir / "raw"
        self.pending_meta_dir = self.pending_dir / "meta"

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "root": str(self.root),
            "counts": self.counts(),
        }

    def count(self) -> int:
        return self.counts()["raw"]

    def counts(self) -> dict[str, int]:
        return {
            "raw": count_files(self.raw_dir, "*.jpg"),
            "meta": count_files(self.meta_dir, "*.json"),
            "pending": count_files(self.pending_raw_dir, "*.jpg"),
        }

    def capture_pending(
        self,
        raw_jpeg: bytes | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return self._write_sample(raw_jpeg, metadata, pending=True)

    def save(
        self,
        raw_jpeg: bytes | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return self._write_sample(raw_jpeg, metadata, pending=False)

    def _write_sample(
        self,
        raw_jpeg: bytes | None,
        metadata: dict[str, Any],
        pending: bool,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise PermissionError("dataset capture is disabled")
        if not raw_jpeg:
            raise RuntimeError("no raw analysis frame available")

        raw_dir = self.pending_raw_dir if pending else self.raw_dir
        meta_dir = self.pending_meta_dir if pending else self.meta_dir
        for path in (raw_dir, meta_dir):
            path.mkdir(parents=True, exist_ok=True)

        light = safe_light_label(str((metadata.get("labels") or {}).get("light") or "unknown"))
        sample_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}_{light}"
        raw_path = raw_dir / f"{sample_id}.jpg"
        meta_path = meta_dir / f"{sample_id}.json"

        raw_path.write_bytes(raw_jpeg)
        meta_path.write_text(json.dumps({"id": sample_id, **metadata}, ensure_ascii=False, indent=2), encoding="utf-8")

        prefix = "pending/" if pending else ""
        return {
            "id": sample_id,
            "pending": pending,
            "raw_path": str(raw_path),
            "meta_path": str(meta_path),
            "raw_url": f"/dataset/{prefix}raw/{sample_id}.jpg",
            "meta_url": f"/dataset/{prefix}meta/{sample_id}.json",
            "counts": self.counts(),
        }

    def confirm(self, sample_id: str) -> dict[str, Any]:
        if not self.enabled:
            raise PermissionError("dataset capture is disabled")
        sample_id = safe_token(sample_id)
        for path in (self.raw_dir, self.meta_dir):
            path.mkdir(parents=True, exist_ok=True)
        moves = (
            (self.pending_raw_dir / f"{sample_id}.jpg", self.raw_dir / f"{sample_id}.jpg"),
            (self.pending_meta_dir / f"{sample_id}.json", self.meta_dir / f"{sample_id}.json"),
        )
        if not moves[0][0].exists():
            raise FileNotFoundError(f"pending sample not found: {sample_id}")
        for src, dst in moves:
            if src.exists():
                shutil.move(str(src), str(dst))
        return {
            "id": sample_id,
            "raw_url": f"/dataset/raw/{sample_id}.jpg",
            "meta_url": f"/dataset/meta/{sample_id}.json",
            "counts": self.counts(),
        }

    def cancel(self, sample_id: str) -> dict[str, Any]:
        if not self.enabled:
            raise PermissionError("dataset capture is disabled")
        sample_id = safe_token(sample_id)
        deleted = delete_sample(
            sample_id,
            self.pending_raw_dir,
            self.pending_meta_dir,
        )
        return {"id": sample_id, "deleted": deleted, "counts": self.counts()}

    def delete(self, sample_id: str) -> dict[str, Any]:
        try:
            deleted = delete_sample(sample_id, self.raw_dir, self.meta_dir)
        except OSError:
            deleted = []
        return {"id": safe_token(sample_id), "deleted": deleted, "counts": self.counts()}


def count_files(path: Path, pattern: str) -> int:
    try:
        return sum(1 for _ in path.glob(pattern))
    except OSError:
        return 0


def delete_sample(sample_id: str, raw_dir: Path, meta_dir: Path) -> list[str]:
    sample_id = safe_token(sample_id)
    deleted: list[str] = []
    for path in (
        raw_dir / f"{sample_id}.jpg",
        meta_dir / f"{sample_id}.json",
    ):
        try:
            path.unlink()
            deleted.append(str(path))
        except FileNotFoundError:
            pass
    return deleted


def safe_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)[:96] or "sample"


def safe_light_label(value: str) -> str:
    token = safe_token(value)
    return token if token in {"day", "night_ir", "low_light"} else "unknown"
