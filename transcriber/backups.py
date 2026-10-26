"""Copies of a transcript's files taken before each change is saved, so a bad save can be undone.

Kept in %APPDATA%\\WhisperTranscriber\\backups\\<transcript>\\<date-time>\\, the newest 20 per transcript.
"""

from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Optional

from transcriber.people import APP_DIR

BACKUP_DIR = APP_DIR / "backups"
KEEP = 20


def _folder_for(files: dict) -> Optional[Path]:
    first = next((Path(f) for f in files.values() if f), None)
    if first is None:
        return None
    from transcriber.names import split_kind
    stem, _kind = split_kind(first.name)
    name = stem if stem is not None else first.name
    where = hashlib.sha1(str(first.parent).lower().encode("utf-8", "replace")).hexdigest()[:8]
    return BACKUP_DIR / f"{name}-{where}"


def backup(files: dict, stamp: Optional[str] = None) -> Optional[Path]:
    """Copy a version's files now. Several versions saved together can share one stamp."""
    folder = _folder_for(files)
    if folder is None:
        return None
    stamp = stamp or new_stamp()
    dest = folder / stamp
    dest.mkdir(parents=True, exist_ok=True)
    for f in files.values():
        if f and Path(f).exists():
            shutil.copy2(f, dest / Path(f).name)
    old = sorted((p for p in folder.iterdir() if p.is_dir()), key=lambda p: p.name)
    for p in old[:-KEEP]:
        shutil.rmtree(p, ignore_errors=True)
    return dest


def list_backups(parts: list) -> list:
    """[(stamp, [(files, backup folder), ...])] newest first, for one or more versions shown together."""
    by_stamp: dict = {}
    for files in parts:
        folder = _folder_for(files)
        if folder and folder.exists():
            for p in folder.iterdir():
                if p.is_dir():
                    by_stamp.setdefault(p.name, []).append((files, p))
    return sorted(by_stamp.items(), key=lambda kv: kv[0], reverse=True)


def new_stamp() -> str:
    """A backup folder name: the date and time, with milliseconds so two saves in the same second
    don't share (and overwrite) one copy."""
    now = time.time()
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{int(now * 1000) % 1000:03d}"


def restore(entries: list) -> None:
    """Put backed-up files back (the current files are backed up first, so this can be undone too).
    Side files the copy didn't have yet (speakers, word timings added later) are removed, so the
    version really looks like it did then."""
    stamp = new_stamp() + "-before-restore"
    for files, folder in entries:
        backup(files, stamp)
        for kind, f in files.items():
            if not f:
                continue
            src = folder / Path(f).name
            if src.exists():
                shutil.copy2(src, f)
            elif kind in ("speakers", "words") and Path(f).exists():
                Path(f).unlink()


def stamp_label(stamp: str) -> str:
    try:
        t = time.strptime(stamp[:15], "%Y%m%d-%H%M%S")
    except ValueError:
        return stamp
    day = time.strftime("%Y%m%d")
    when = time.strftime("%I:%M:%S %p", t).lstrip("0")
    label = f"Today {when}" if stamp.startswith(day) else time.strftime("%b %d, ", t) + when
    return label + ("  (before a restore)" if stamp.endswith("before-restore") else "")
