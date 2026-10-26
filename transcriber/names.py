"""File names of saved transcripts, in one place:

    Talk_transcript.txt        a version's text      (also Talk.srt / Talk.vtt)
    Talk_speakers.json         its master list of lines (and who's talking)
    Talk_words.json            its word timings
    Talk (2)_transcript.txt    version 2
    Talk [Mic]_transcript.txt  one audio track ("Mic") transcribed on its own
    Talk_session.json          a combined recording's list of files
    Talk_markers.json          markers (shared by every version and track)

Each recording has its own folder named after it: Transcripts/Talk/."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

KINDS = {"_transcript.txt": "txt", "_speakers.json": "speakers", "_words.json": "words",
         "_session.json": "session", "_markers.json": "markers", ".srt": "srt", ".vtt": "vtt"}
SIDECAR_ENDS = tuple(KINDS)

VERSION_SUFFIX = re.compile(r"^(?P<base>.*) \((?P<n>\d+)\)$")
TRACK_SUFFIX = re.compile(r"^(?P<base>.*) \[(?P<track>[^\[\]]+)\]$")


def split_kind(name: str) -> tuple:
    """'Talk (2)_words.json' -> ('Talk (2)', 'words'); (None, None) for other files."""
    low = name.lower()
    for end, kind in KINDS.items():
        if low.endswith(end):
            return name[: -len(end)], kind
    return None, None


def sibling(path, kind: str) -> Path:
    """The same version's file of another kind: sibling('Talk (2)_transcript.txt', 'words')."""
    p = Path(path)
    stem, _k = split_kind(p.name)
    end = next(e for e, k in KINDS.items() if k == kind)
    return p.with_name((stem if stem is not None else p.stem) + end)


def stem_of(path) -> str:
    """A file's name without its extension, for Windows paths too (on any system)."""
    name = re.split(r"[\\/]", str(path or ""))[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def own_number(key: str, source) -> bool:
    """True when a name's " (2)" is part of the recording's own file name (Windows names copies
    "Interview (2).mp4") rather than this app's version number."""
    return bool(source) and VERSION_SUFFIX.match(key) is not None and stem_of(source) == key


def base_name(filename: str, keep_number: bool = False) -> Optional[str]:
    """The recording a transcript file belongs to: 'Talk [Mic] (2)_transcript.txt' -> 'Talk'.
    keep_number: the " (2)" is the recording's own (see own_number)."""
    name, kind = split_kind(filename)
    if kind is None:
        return None
    if not keep_number:
        name = re.sub(r" \(\d+\)$", "", name)      # version number
    name = re.sub(r" \[[^\[\]]+\]$", "", name)     # track
    if not keep_number:
        name = re.sub(r" \(\d+\)$", "", name)
    return name or None


def recording_folder(output_dir, name: str) -> Path:
    clean = re.sub(r'[<>:"/\\|?*]+', " ", name).strip().rstrip(". ") or "Transcript"
    return Path(output_dir) / clean


def markers_path(files: dict) -> Optional[Path]:
    """'<name>_markers.json' for a version: one per recording, shared by its versions and tracks.
    (A recording's folder is named after it, so a folder called 'Interview (2)' means the " (2)"
    is part of the name, not a version number.)"""
    first = next((Path(f) for f in files.values() if f), None)
    if first is None:
        return None
    folder = first.parent.name
    stem, _k = split_kind(first.name)
    name = stem if stem is not None else first.stem
    keep = folder == name or base_name(first.name, keep_number=True) == folder
    return first.with_name(f"{base_name(first.name, keep_number=keep) or name}_markers.json")
