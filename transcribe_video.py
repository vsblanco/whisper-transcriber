"""Command-line version of Whisper Transcriber.

Usage:
    python transcribe_video.py "video.mp4"
    python transcribe_video.py "video.mkv" --model large
    python transcribe_video.py "a.mp4" "b.mp3" --formats txt srt --output-dir "D:\\Transcripts"

Transcripts go to Documents\\Transcripts unless --output-dir or --output is given.
"""

import argparse
import sys
from pathlib import Path

from transcriber.core import (
    MODELS,
    Segment,
    check_ffmpeg,
    default_output_dir,
    format_timestamp,
    transcribe_file,
)
from transcriber.files import WRITERS, save_transcript


def main() -> int:
    p = argparse.ArgumentParser(description="Transcribe video or audio files with Whisper.")
    p.add_argument("files", nargs="+", help="Video or audio files to transcribe")
    p.add_argument("--model", default="medium", choices=MODELS, help="Whisper model (default: medium)")
    p.add_argument("--language", default=None, help="Language code like en or es (default: auto-detect)")
    p.add_argument("--formats", nargs="+", default=["txt"], choices=list(WRITERS), help="Output formats (default: txt)")
    p.add_argument("--output-dir", default=None, help=f"Folder for transcripts (default: {default_output_dir()})")
    p.add_argument("--output", "-o", default=None, help="Exact .txt path (single file only; kept for the old script)")
    args = p.parse_args()

    if not check_ffmpeg():
        print("ERROR: ffmpeg/ffprobe not found. Install ffmpeg and add it to your PATH.")
        return 1

    failures = 0
    for f in args.files:
        print(f"\n=== {f}")

        def show(seg: Segment):
            print(f"[{format_timestamp(seg.start)}] {seg.text}")

        try:
            t = transcribe_file(f, args.model, args.language, on_segment=show)
        except Exception as exc:
            print(f"ERROR: {exc}")
            failures += 1
            continue

        if args.output and len(args.files) == 1:
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(WRITERS["txt"][1](t), encoding="utf-8")
            saved = [out]
        else:
            saved = save_transcript(t, Path(args.output_dir or default_output_dir()), args.formats)
        for s in saved:
            print(f"Saved: {s}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
