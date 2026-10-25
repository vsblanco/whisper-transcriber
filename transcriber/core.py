"""Core transcription logic: audio extraction, Whisper transcription, and output writers.

Shared by the desktop app (app.py) and the command-line tool (transcribe_video.py).
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

MODELS = ["tiny", "base", "small", "medium", "large", "turbo"]
MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm", ".wmv", ".flv",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
}

# On Windows, keep ffmpeg/ffprobe from flashing a console window when run from the GUI.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class TranscriptionCancelled(Exception):
    """Raised inside a running transcription when the user cancels."""


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    source: str
    model: str
    language: str
    segments: list[Segment] = field(default_factory=list)
    text: str = ""


# --------------------------------------------------------------------------- helpers

def default_output_dir() -> Path:
    """Documents\\Transcripts for the current user."""
    return Path.home() / "Documents" / "Transcripts"


def format_timestamp(seconds: float, millis: bool = False, sep: str = ",") -> str:
    """HH:MM:SS, or HH:MM:SS,mmm when millis=True (sep '.' for WebVTT)."""
    total_ms = int(round(max(seconds, 0) * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    if millis:
        return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


def check_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, creationflags=_NO_WINDOW)
        subprocess.run(["ffprobe", "-version"], capture_output=True, creationflags=_NO_WINDOW)
        return True
    except FileNotFoundError:
        return False


def media_duration(path: str) -> Optional[float]:
    """Length of the media file in seconds, or None if ffprobe can't tell."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, creationflags=_NO_WINDOW,
        ).stdout.strip()
        return float(out) if out else None
    except (FileNotFoundError, ValueError):
        return None


def extract_audio(media_path: str, audio_path: str) -> None:
    """Mix every audio track in the file down to a 16 kHz mono WAV for Whisper."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
         "-of", "csv=p=0", media_path],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    )
    streams = [s.strip() for s in probe.stdout.splitlines() if s.strip()]
    if not streams:
        raise RuntimeError("No audio track found in this file.")

    cmd = ["ffmpeg", "-y", "-i", media_path]
    if len(streams) > 1:
        inputs = "".join(f"[0:{s}]" for s in streams)
        cmd += ["-filter_complex", f"{inputs}amix=inputs={len(streams)}:duration=longest"]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", audio_path]

    result = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW)
    if result.returncode != 0 or not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
        tail = (result.stderr or "").strip().splitlines()[-3:]
        raise RuntimeError("ffmpeg could not extract the audio.\n" + "\n".join(tail))


# --------------------------------------------------------------------------- transcription

_model_cache: dict[str, object] = {}

# Whisper's verbose output looks like: [00:12.340 --> 00:15.870]  Some text
_SEGMENT_LINE = re.compile(r"^\[(?:(\d+):)?(\d+):(\d+\.\d+) --> (?:(\d+):)?(\d+):(\d+\.\d+)\]\s*(.*)$")


def _to_seconds(h: Optional[str], m: str, s: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + float(s)


class _SegmentStream(io.TextIOBase):
    """Captures Whisper's verbose stdout and turns each line into a live segment callback.

    Also the cancellation point: raising from write() aborts the running transcription.
    """

    def __init__(self, on_segment: Optional[Callable[[Segment], None]], should_cancel: Callable[[], bool]):
        self._buf = ""
        self._on_segment = on_segment
        self._should_cancel = should_cancel

    def write(self, s: str) -> int:
        if self._should_cancel():
            raise TranscriptionCancelled()
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            m = _SEGMENT_LINE.match(line.strip())
            if m and self._on_segment:
                start = _to_seconds(m.group(1), m.group(2), m.group(3))
                end = _to_seconds(m.group(4), m.group(5), m.group(6))
                self._on_segment(Segment(start, end, m.group(7).strip()))
        return len(s)


def load_model(name: str, log: Callable[[str], None] = print):
    if name not in _model_cache:
        import whisper  # imported lazily so the app opens fast
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        log(f"Loading Whisper '{name}' model on {device.upper()} "
            f"(the first time downloads it, which can take a few minutes)...")
        _model_cache[name] = whisper.load_model(name, device=device)
    return _model_cache[name]


def whisper_device() -> str:
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "unknown"


def transcribe_file(
    media_path: str,
    model_name: str = "medium",
    language: Optional[str] = None,
    on_segment: Optional[Callable[[Segment], None]] = None,
    should_cancel: Callable[[], bool] = lambda: False,
    log: Callable[[str], None] = print,
) -> Transcript:
    """Transcribe one audio or video file. Calls on_segment as each line is recognized."""
    if not os.path.exists(media_path):
        raise FileNotFoundError(media_path)

    fd, audio_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        log("Extracting audio...")
        extract_audio(media_path, audio_path)
        if should_cancel():
            raise TranscriptionCancelled()

        model = load_model(model_name, log)
        log("Transcribing...")
        stream = _SegmentStream(on_segment, should_cancel)
        with contextlib.redirect_stdout(stream):
            result = model.transcribe(
                audio_path,
                language=language or None,
                verbose=True,                       # prints each segment; captured above
                fp16=whisper_device() == "cuda",    # fp16 only works on GPU
            )
    finally:
        with contextlib.suppress(OSError):
            os.remove(audio_path)

    return Transcript(
        source=media_path,
        model=model_name,
        language=result.get("language", language or ""),
        segments=[Segment(s["start"], s["end"], s["text"].strip()) for s in result["segments"]],
        text=result["text"].strip(),
    )


# --------------------------------------------------------------------------- writers

def render_txt(t: Transcript) -> str:
    """Same layout as the original script: header, timestamped lines, then full text."""
    lines = [
        f"Transcript of: {t.source}",
        f"Model: {t.model}",
        f"Language: {t.language}",
        "=" * 60,
        "",
        *[f"[{format_timestamp(s.start)}] {s.text}" for s in t.segments],
        "",
        "=" * 60,
        "Full text:",
        "",
        t.text,
    ]
    return "\n".join(lines) + "\n"


def render_srt(t: Transcript) -> str:
    blocks = []
    for i, s in enumerate(t.segments, 1):
        blocks.append(
            f"{i}\n{format_timestamp(s.start, True)} --> {format_timestamp(s.end, True)}\n{s.text}\n"
        )
    return "\n".join(blocks)


def render_vtt(t: Transcript) -> str:
    blocks = ["WEBVTT\n"]
    for s in t.segments:
        blocks.append(
            f"{format_timestamp(s.start, True, '.')} --> {format_timestamp(s.end, True, '.')}\n{s.text}\n"
        )
    return "\n".join(blocks)


WRITERS = {
    "txt": ("_transcript.txt", render_txt),
    "srt": (".srt", render_srt),
    "vtt": (".vtt", render_vtt),
}


def _unique_path(path: Path) -> Path:
    """Add ' (2)', ' (3)'... instead of overwriting an existing transcript."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    n = 2
    while True:
        candidate = path.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def save_transcript(t: Transcript, output_dir: Path, formats: list[str], overwrite: bool = False) -> list[Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(t.source).stem
    saved = []
    for fmt in formats:
        suffix, render = WRITERS[fmt]
        path = output_dir / f"{stem}{suffix}"
        if not overwrite:
            path = _unique_path(path)
        path.write_text(render(t), encoding="utf-8")
        saved.append(path)
    return saved
