#!/usr/bin/env python3
"""Download Instagram reel audio and transcribe it with Whisper.

This is an operational helper for the extracted Chrome reel set saved in
``/tmp/ig_final_content.json``. It keeps the flow deterministic:

1. Read reel URLs from the extraction artifact
2. Download best-audio with Chrome cookies via yt-dlp
3. Transcribe with Whisper
4. Write a manifest containing URL, audio path, transcript path, and transcript

Example:
    python3 scripts/ops/transcribe_instagram_reels.py --limit 3
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_INPUT = Path("/tmp/ig_final_content.json")
DEFAULT_AUDIO_DIR = Path("/tmp/ig_reel_audio")
DEFAULT_TRANSCRIPT_DIR = Path("/tmp/ig_reel_transcripts")
DEFAULT_MANIFEST = Path("/tmp/ig_audio_transcripts.json")


def _load_items(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError(f"Expected list in {path}, got {type(data).__name__}")
    return [item for item in data if "instagram.com" in str(item.get("url", ""))]


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _download_audio(item: dict, audio_dir: Path) -> Path:
    tab = int(item["tab"])
    stem = audio_dir / f"tab_{tab:02d}"
    output_template = str(stem) + ".%(ext)s"
    _run(
        [
            sys.executable,
            "-m",
            "yt_dlp",
            "--cookies-from-browser",
            "chrome",
            "-f",
            "bestaudio",
            "-o",
            output_template,
            item["url"],
        ]
    )
    matches = sorted(audio_dir.glob(f"tab_{tab:02d}.*"))
    for match in matches:
        if match.suffix != ".part":
            return match
    raise FileNotFoundError(f"No downloaded audio found for tab {tab}")


def _transcribe_audio(audio_path: Path, transcript_dir: Path, model: str) -> Path:
    whisper_bin = shutil.which("whisper")
    if not whisper_bin:
        raise FileNotFoundError("whisper executable not found in PATH")

    transcript_dir.mkdir(parents=True, exist_ok=True)
    _run(
        [
            whisper_bin,
            str(audio_path),
            "--model",
            model,
            "--language",
            "en",
            "--task",
            "transcribe",
            "--output_dir",
            str(transcript_dir),
            "--output_format",
            "txt",
        ]
    )
    txt_path = transcript_dir / f"{audio_path.stem}.txt"
    if not txt_path.exists():
        raise FileNotFoundError(f"Expected transcript not found: {txt_path}")
    return txt_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--audio-dir", type=Path, default=DEFAULT_AUDIO_DIR)
    parser.add_argument("--transcript-dir", type=Path, default=DEFAULT_TRANSCRIPT_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--model", default="tiny.en")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--tabs", nargs="*", type=int)
    args = parser.parse_args()

    items = _load_items(args.input)
    selected_tabs = set(args.tabs or [])
    if selected_tabs:
        items = [item for item in items if int(item["tab"]) in selected_tabs]
    if args.limit:
        items = items[: args.limit]

    args.audio_dir.mkdir(parents=True, exist_ok=True)
    args.transcript_dir.mkdir(parents=True, exist_ok=True)

    manifest: list[dict] = []
    for item in items:
        audio_path = _download_audio(item, args.audio_dir)
        transcript_path = _transcribe_audio(audio_path, args.transcript_dir, args.model)
        manifest.append(
            {
                "tab": item["tab"],
                "url": item["url"],
                "title": item.get("og_title", ""),
                "description": item.get("og_description", ""),
                "audio_path": str(audio_path),
                "transcript_path": str(transcript_path),
                "transcript": transcript_path.read_text(),
            }
        )
        args.manifest.write_text(json.dumps(manifest, indent=2))

    print(f"Wrote {len(manifest)} transcripts to {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
