"""Recordings made at the same time on separate devices (a lav mic per person, a camera, isolated
game captures), combined into one transcript.

- Each file is one person. Files are lined up by matching their sound (mics pick up each other a
  little), or by their recording start times when there's no shared sound.
- Lines a mic picked up from someone else are dropped: at that moment the other person's own mic
  was clearly louder.
- '<name>_session.json' next to the transcripts records the files, who they are and the offsets.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from transcriber.core import _NO_WINDOW, write_text_safe

ENV_RATE = 50            # loudness samples per second (20 ms steps)
MAX_ANALYZE = 30 * 60    # seconds of audio used for syncing
BLEED_DB = 5.0           # someone else's mic louder by this much = the line was picked up from them


def envelope(path: str, seconds: Optional[float] = None):
    """Loudness over time in dB, ENV_RATE values per second (all of a file's audio mixed)."""
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le"]
    if seconds:
        cmd[3:3] = ["-t", str(seconds)]
    cmd.append("-")
    raw = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW).stdout
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    step = 8000 // ENV_RATE
    n = len(data) // step
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    frames = data[: n * step].reshape(n, step)
    rms = np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-6
    return (20 * np.log10(rms)).astype(np.float32)


def creation_time(path: str) -> Optional[float]:
    """When the device says recording started (Unix seconds), if the file records it."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=creation_time",
                              "-of", "default=nw=1:nk=1", path],
                             capture_output=True, text=True, creationflags=_NO_WINDOW).stdout.strip()
        if not out:
            return None
        from datetime import datetime
        return datetime.fromisoformat(out.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _lag(ref, other):
    """How many envelope steps `other` starts after `ref`, and how sure we are (peak vs. noise)."""
    import numpy as np
    a = ref - ref.mean()
    b = other - other.mean()
    if not len(a) or not len(b) or a.std() < 1e-3 or b.std() < 1e-3:
        return 0, 0.0
    n = 1
    while n < len(a) + len(b):
        n *= 2
    corr = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    corr = np.concatenate([corr[-(len(b) - 1):], corr[: len(a)]])     # lags from -(len(b)-1) to len(a)-1
    def alike(k):
        """How alike the two are where they overlap at this lag (1 = same shape, 0 = unrelated)."""
        lag = k - (len(b) - 1)
        if lag >= 0:
            x, y = a[lag: lag + len(b)], b[: len(a) - lag]
        else:
            x, y = a[: len(b) + lag], b[-lag: -lag + len(a)]
        m = min(len(x), len(y))
        if m < ENV_RATE * 3:
            return 0.0
        r = float(np.corrcoef(x[:m], y[:m])[0, 1])
        return r if r == r else 0.0

    k = int(np.argmax(corr))
    best = alike(k)
    # The runner-up at least 2 s away: a real match stands out; unrelated recordings (isolated
    # mics with nothing in common) give several similar, weak peaks.
    masked = corr.copy()
    masked[max(k - 2 * ENV_RATE, 0): k + 2 * ENV_RATE] = -np.inf
    second = alike(int(np.argmax(masked))) if np.isfinite(masked).any() else 0.0
    score = best if best - second >= 0.15 else 0.0
    return k - (len(b) - 1), score


def find_offsets(paths: list, ref: int = 0, log=lambda m: None) -> list:
    """Where each file starts on the shared timeline, in seconds (the reference starts at 0), plus
    how each was found: "sound", "clock" (recording start times) or "start" (assumed together)."""
    envs = []
    for p in paths:
        log(f"Listening to {Path(p).name}...")
        envs.append(envelope(p, MAX_ANALYZE))
    clocks = [creation_time(p) for p in paths]
    out = []
    for i, p in enumerate(paths):
        if i == ref:
            out.append((0.0, "sound"))
            continue
        lag, score = _lag(envs[ref], envs[i])
        if score >= 0.6:
            out.append((lag / ENV_RATE, "sound"))
        elif clocks[ref] is not None and clocks[i] is not None:
            out.append((clocks[i] - clocks[ref], "clock"))
        else:
            out.append((0.0, "start"))
    return out


def drop_crosstalk(parts: list) -> list:
    """parts = [(segments, offset, envelope)] with segment times already on the shared timeline.
    Returns the segments to keep for each part: a line goes when another mic was clearly louder
    then (it was that person talking, picked up by this mic)."""
    import numpy as np

    def level(env, offset, start, end):
        a = int(max((start - offset) * ENV_RATE, 0))
        b = int(max((end - offset) * ENV_RATE, a + 1))
        chunk = env[a:b]
        return float(np.percentile(chunk, 90)) if len(chunk) else -120.0

    kept = []
    for i, (segs, off, env) in enumerate(parts):
        keep = []
        for s in segs:
            mine = level(env, off, s.start, s.end)
            loudest_other = max((level(e2, o2, s.start, s.end) for j, (_s2, o2, e2) in enumerate(parts) if j != i),
                                default=-120.0)
            if loudest_other - mine < BLEED_DB:
                keep.append(s)
        kept.append(keep)
    return kept


def session_path(folder, name: str) -> Path:
    return Path(folder) / f"{name}_session.json"


def write_session(path, *, name: str, files: list, video: Optional[str], reference: Optional[str] = None) -> None:
    """files = [{"path", "label", "person", "offset"}] (offset = where the file starts, in seconds).
    reference: the file whose timeline the transcript uses, when it isn't the video or the first mic
    (recordings added to an existing transcript keep that transcript's timeline)."""
    data = {"version": 1, "name": name, "video": video, "files": files}
    if reference:
        data["reference"] = reference
    write_text_safe(path, json.dumps(data))


def read_session(path) -> Optional[dict]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def reference_file(data: dict) -> Optional[str]:
    """The file whose timeline the transcript uses (and the player shows): the video, else the mic
    that starts first."""
    if data.get("reference"):
        return data["reference"]
    if data.get("video"):
        return data["video"]
    files = sorted(data.get("files") or [], key=lambda f: f.get("offset", 0))
    return files[0]["path"] if files else None
