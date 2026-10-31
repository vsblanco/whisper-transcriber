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


def envelope(path: str, seconds: Optional[float] = None, stream: Optional[int] = None):
    """Loudness over time in dB, ENV_RATE values per second (all of a file's audio mixed, or one
    audio track: stream = its number)."""
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-i", path] + (["-map", f"0:a:{stream}"] if stream is not None else []) + \
        ["-vn", "-ac", "1", "-ar", "8000", "-f", "s16le"]
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


def _words(text: str) -> list:
    import re
    return re.findall(r"[a-z0-9']+", text.lower().replace("\u2019", "'"))


def find_crosstalk(tracks: list, level=None) -> list:
    """Lines that are another mic picking up someone, in a recording with a mic per person.

    tracks = [[(start, end, text), ...] per mic], times on the shared timeline.
    level(mic, start, end) -> that mic's loudness then, relative to its own usual speaking level
    (dB), or None when it can't tell.

    A line counts as picked up only when the same words were caught by another mic at the same
    moment (most of its words appear in that mic's lines then), and that other mic was the louder
    one, for its own level. A line no other mic has is never removed: those words exist only there.
    Returns [set of line indexes to remove] per mic."""
    import bisect
    words = [[_words(t) for _a, _b, t in tr] for tr in tracks]
    starts = [[float(a) for a, _b, _t in tr] for tr in tracks]
    longest = [max((float(b) - float(a) for a, b, _t in tr), default=0.0) for tr in tracks]
    drop = [set() for _ in tracks]
    partners: dict = {}
    for i, tr in enumerate(tracks):
        for k, (a, b, _t) in enumerate(tr):
            a, b = float(a), float(b)
            mine = words[i][k]
            if not mine:
                continue
            short = len(mine) <= 2
            slack = 0.4 if short else 0.75
            for j, other in enumerate(tracks):
                if j == i:
                    continue
                lo = bisect.bisect_left(starts[j], a - slack - longest[j])
                hi = bisect.bisect_right(starts[j], b + slack)
                near = [m for m in range(lo, hi) if float(other[m][1]) > a - slack and float(other[m][0]) < b + slack]
                if short:                       # a word or two: only the same words, starting together
                    near = [m for m in near if abs(float(other[m][0]) - a) < slack and words[j][m] == mine]
                if not near:
                    continue
                pool: dict = {}
                for m in near:
                    for w in words[j][m]:
                        pool[w] = pool.get(w, 0) + 1
                shared = 0
                for w in mine:
                    if pool.get(w, 0) > 0:
                        pool[w] -= 1
                        shared += 1
                if shared / len(mine) < 0.6:
                    continue
                if level is None:
                    continue
                here, there = level(i, a, b), level(j, a, b)
                if here is None or there is None:
                    continue
                if there > here + 1.0:           # the other mic was the closer one: picked up from them
                    drop[i].add(k)
                    partners.setdefault((i, k), []).append((j, near))
    # Never lose the words altogether: if every copy elsewhere is going too, keep this one.
    for (i, k), found in partners.items():
        if all(all(m in drop[j] for m in near) for j, near in found):
            drop[i].discard(k)
    return drop


def relative_levels(envs: list, offsets: list):
    """level(mic, start, end) for find_crosstalk: each mic's loudness then (the loud part of the
    stretch) minus its own usual speaking level, so mics set to different volumes compare fairly."""
    import numpy as np
    refs = []
    for env in envs:
        if env is None or not len(env):
            refs.append(None)
            continue
        loud = env[env > np.percentile(env, 60)]
        refs.append(float(np.percentile(loud, 90)) if len(loud) else float(np.max(env)))

    def level(mic, start, end):
        env, ref = envs[mic], refs[mic]
        if env is None or ref is None:
            return None
        a = int(max((start - offsets[mic]) * ENV_RATE, 0))
        b = int(max((end - offsets[mic]) * ENV_RATE, a + 1))
        chunk = env[a:b]
        if not len(chunk):
            return None
        return float(np.percentile(chunk, 90)) - ref
    return level


def drop_crosstalk(parts: list) -> list:
    """parts = [(segments, offset, envelope)] with segment times already on the shared timeline.
    Returns the segments to keep for each part (see find_crosstalk)."""
    tracks = [[(s.start, s.end, s.text) for s in segs] for segs, _off, _env in parts]
    level = relative_levels([env for _s, _o, env in parts], [off for _s, off, _e in parts])
    drop = find_crosstalk(tracks, level)
    return [[s for k, s in enumerate(segs) if k not in drop[n]] for n, (segs, _o, _e) in enumerate(parts)]


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
