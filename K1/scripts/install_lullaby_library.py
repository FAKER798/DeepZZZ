from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import wave


def install_library(source_dir: Path, output_dir: Path) -> list[dict[str, object]]:
    sources = sorted(source_dir.glob("*.mp3"), key=lambda path: path.name)
    if not sources:
        raise RuntimeError(f"no mp3 files found in {source_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    tracks: list[dict[str, object]] = []
    for index, source in enumerate(sources):
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        output = output_dir / f"{digest}.wav"
        temp = output.with_suffix(".wav.tmp")
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source), "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", "-f", "wav", str(temp),
            ],
            check=True,
        )
        temp.replace(output)
        with wave.open(str(output), "rb") as audio:
            duration_s = audio.getnframes() / max(1, audio.getframerate())
        updated_at = now - index
        tracks.append(
            {
                "id": digest,
                "title": source.stem,
                "file": str(output),
                "audio_url": f"/lullabies/{output.name}",
                "source_file": str(source),
                "bytes": source.stat().st_size,
                "stored_bytes": output.stat().st_size,
                "sample_rate": 48000,
                "playback_sample_rate": 48000,
                "playback_channels": 2,
                "duration_s": round(duration_s, 3),
                "created_at": updated_at,
                "updated_at": updated_at,
            }
        )
    manifest = {"version": 3, "updated_at": now, "tracks": tracks}
    manifest_file = output_dir / "manifest.json"
    manifest_temp = output_dir / "manifest.json.tmp"
    manifest_temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_temp.replace(manifest_file)
    keep = {Path(str(track["file"])).name for track in tracks}
    for old_file in output_dir.glob("*.wav"):
        if old_file.name not in keep:
            old_file.unlink()
    return tracks


def main() -> int:
    parser = argparse.ArgumentParser(description="Decode the fixed lullaby library for board playback")
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    tracks = install_library(args.source_dir, args.output_dir)
    for track in tracks:
        print(f"{track['title']}\t{track['duration_s']}s\t{track['stored_bytes']} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
