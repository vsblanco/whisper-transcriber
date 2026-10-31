"""A copy of a video with the captions inside it as a subtitle track (soft subtitles).

The picture and sound are copied as they are (nothing is re-encoded, so it's quick and loses no
quality); the captions go in as their own track that players and TVs list under Subtitles / CC
and can turn on or off:

- .mkv and everything else: an .mkv with an SRT track (VLC, Plex, Jellyfin, Kodi, most smart TVs)
- .mp4 / .m4v: an .mp4 with a mov_text track (iPhone, QuickTime, many TVs)
- .mov: a .mov with a mov_text track

The original file is never touched.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable, Optional

from transcriber.core import _NO_WINDOW

# Whisper's language codes are two letters; video files want three.
_ISO3 = {"en": "eng", "es": "spa", "fr": "fra", "de": "deu", "it": "ita", "pt": "por", "nl": "nld", "ru": "rus",
         "ja": "jpn", "ko": "kor", "zh": "zho", "ar": "ara", "hi": "hin", "tr": "tur", "pl": "pol", "uk": "ukr",
         "sv": "swe", "da": "dan", "no": "nor", "fi": "fin", "el": "ell", "he": "heb", "id": "ind", "vi": "vie",
         "th": "tha", "cs": "ces", "ro": "ron", "hu": "hun", "tl": "tgl", "ca": "cat"}

_NAMES = {"eng": "English", "spa": "Spanish", "fra": "French", "deu": "German", "ita": "Italian",
          "por": "Portuguese", "jpn": "Japanese", "kor": "Korean", "zho": "Chinese", "rus": "Russian"}


def language_code(lang: str) -> str:
    """'en' / 'english' / 'eng' -> 'eng'; unknown -> 'und' (players show it as Unknown)."""
    lang = (lang or "").strip().lower()
    if not lang:
        return "und"
    if len(lang) == 3 and lang.isalpha():
        return lang
    if lang in _ISO3:
        return _ISO3[lang]
    for three, name in _NAMES.items():
        if name.lower() == lang:
            return three
    return "und"


def output_for(source: str) -> tuple[str, str]:
    """(file extension, subtitle codec) for a copy of this video with a subtitle track."""
    ext = Path(source).suffix.lower()
    if ext in (".mp4", ".m4v"):
        return ".mp4" if ext == ".mp4" else ".m4v", "mov_text"
    if ext == ".mov":
        return ".mov", "mov_text"
    return ".mkv", "srt"


def default_name(source: str) -> str:
    ext, _codec = output_for(source)
    return f"{Path(source).stem} (captions){ext}"


def mux_command(source: str, srt: str, out: str, language: str = "", title: str = "",
                default: bool = True) -> list:
    """The ffmpeg command: every video and audio track copied, plus the captions as a subtitle
    track (shown by default when `default`)."""
    _ext, codec = output_for(out if Path(out).suffix else source)
    lang = language_code(language)
    title = title or (_NAMES.get(lang, "") + " captions").strip()
    return ["ffmpeg", "-y", "-v", "error", "-nostdin",
            "-i", source, "-sub_charenc", "UTF-8", "-i", srt,
            "-map", "0:v", "-map", "0:a?", "-map", "1:0",
            "-c", "copy", "-c:s", codec,
            "-metadata:s:s:0", f"language={lang}", "-metadata:s:s:0", f"title={title}",
            "-disposition:s:0", "default" if default else "0",
            "-progress", "pipe:1", "-nostats", out]


def part_path(out: str) -> str:
    """Where it's written first (renamed when done, so a failed save never leaves half a file)."""
    p = Path(out)
    return str(p.with_name(p.stem + ".partial" + p.suffix))


def run(cmd: list, duration: Optional[float], progress: Optional[Callable[[float], None]] = None) -> str:
    """Run the command; progress(0..1) as it goes. Returns '' or what went wrong."""
    out = cmd[-1]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    except OSError as exc:
        return str(exc)
    for line in p.stdout:
        if progress and duration and line.startswith("out_time_us="):
            try:
                progress(min(int(line.split("=", 1)[1]) / 1e6 / duration, 1.0))
            except ValueError:
                pass
    err = p.stderr.read().strip()
    code = p.wait()
    if code:
        try:
            os.remove(out)
        except OSError:
            pass
        lines = err.splitlines()
        return lines[-1] if lines else f"ffmpeg stopped (code {code})"
    return ""
