"""Lining up line times with the actual voice.

Whisper's times can be off by a second or so, especially where a line starts (its first word often
gets the time the previous sentence ended). Here the audio itself decides: each line's start moves
to the nearest moment the voice starts after a pause, and its end to where the voice stops, as long
as that's close to what Whisper said."""

from __future__ import annotations

from typing import Optional

FPS = 50                 # loudness samples per second (envelope() in session.py)
MIN_GAP = 0.25           # this much quiet counts as a pause between phrases
REACH = 2.5              # how far (seconds) a start may move


def speech_mask(path: str, seconds: Optional[float] = None):
    """True where someone is talking, FPS values per second (loudness well above the quiet parts)."""
    import numpy as np
    from transcriber.session import envelope
    env = envelope(path, seconds)
    if not len(env):
        return env.astype(bool)
    loud = float(np.percentile(env, 95))
    return env > loud - 22.0


def _edges(mask):
    """(onsets, offsets) in seconds: where the voice starts after a pause, and stops before one."""
    gap = int(MIN_GAP * FPS)
    on, off = [], []
    n = len(mask)
    quiet_run = gap                       # the recording's start counts as after a pause
    for i in range(n):
        if mask[i]:
            if quiet_run >= gap:
                on.append(i / FPS)
            quiet_run = 0
        else:
            quiet_run += 1
            if quiet_run == gap:
                off.append((i - gap + 1) / FPS)
    if n and mask[-1]:
        off.append(n / FPS)
    return on, off


def _closest(points, target, lo, hi):
    best = None
    for p in points:
        if lo <= p <= hi and (best is None or abs(p - target) < abs(best - target)):
            best = p
    return best


def snap(rows: list, mask, offset: float = 0.0, texts: Optional[list] = None) -> list:
    """rows = [(start, end)] as Whisper (or the words) say; returns [(start, end)] lined up with the
    voice. offset: where the audio file starts on the transcript's timeline. texts: the lines' words,
    so each line gets about as much talking as its length needs (picks between two nearby pauses)."""
    import numpy as np
    if not rows:
        return []
    on, off = _edges(mask)
    on = [t + offset for t in on]
    off = [t + offset for t in off]
    talk = np.concatenate([[0], np.cumsum(np.asarray(mask, dtype=float))]) / FPS

    def speech(a, b):                        # seconds of talking between two timeline times
        i = min(max(int(round((a - offset) * FPS)), 0), len(talk) - 1)
        j = min(max(int(round((b - offset) * FPS)), 0), len(talk) - 1)
        return max(talk[j] - talk[i], 0.0)

    n = len(rows)
    counts = [len(t.split()) for t in texts] if texts and len(texts) == n else None
    rate = None
    if counts:
        total = speech(rows[0][0] - 1, rows[-1][1] + 1)
        rate = sum(counts) / total if total > 1 else None

    # Candidate starts per line: the voice starting after a pause near Whisper's time, or Whisper's
    # own time (for lines that start mid-flow). Then the cheapest in-order choice for all lines.
    cands = []
    for s, _e in rows:
        c = [(p, abs(p - s)) for p in on if s - REACH <= p <= s + REACH]
        # Keeping Whisper's time is for lines that start mid-flow with no pause nearby to snap to.
        mid_flow = speech(s - 0.15, s + 0.15) > 0.15
        near = any(abs(p - s) <= 2.0 for p, _w in c)
        c.append((s, 3.0 if not mid_flow else 2.5 if near else 1.0))
        cands.append(c)
    INF = float("inf")
    cost = [[INF] * len(c) for c in cands]
    back = [[-1] * len(c) for c in cands]
    cost[0] = [w for _p, w in cands[0]]
    for k in range(1, n):
        for j, (tj, wj) in enumerate(cands[k]):
            for i, (ti, _wi) in enumerate(cands[k - 1]):
                if cost[k - 1][i] == INF or tj < ti + 0.3:
                    continue
                c = cost[k - 1][i] + wj
                if rate:
                    c += abs(speech(ti, tj) - counts[k - 1] / rate)
                if c < cost[k][j]:
                    cost[k][j], back[k][j] = c, i
    j = min(range(len(cands[-1])), key=lambda x: cost[-1][x])
    if cost[-1][j] == INF:                       # nothing fits in order: keep Whisper's starts
        starts = [s for s, _e in rows]
    else:
        starts = [0.0] * n
        for k in range(n - 1, -1, -1):
            starts[k] = cands[k][j][0]
            j = back[k][j]

    out = []
    for k, (s, e) in enumerate(rows):
        ns = starts[k]
        if k + 1 < n:                            # until the voice stops before the next line starts
            nxt = starts[k + 1]
            ends = [p for p in off if ns + 0.3 <= p <= nxt]
            ne = min(ends[-1] + 0.1, nxt) if ends else min(max(e, ns + 0.3), nxt)
        else:
            ne = _closest(off, e, ns + 0.3, e + REACH)
            ne = max(e, ns + 0.3) if ne is None else ne + 0.1
        lead = 0.05 if ns in on else 0.0
        out.append((round(max(ns - lead, 0.0), 3), round(ne, 3)))
    for k in range(len(out) - 1):              # no overlaps
        if out[k][1] > out[k + 1][0]:
            out[k] = (out[k][0], max(out[k][0] + 0.2, out[k + 1][0]))
    return out


def row_spans(segs, words) -> list:
    """[(start, end)] for lines: from their word times when there are any, else the line's own."""
    spans = []
    for k, sg in enumerate(segs):
        ws = words[k] if words and k < len(words) else None
        if ws:
            spans.append((float(ws[0][0]), float(ws[-1][1])))
        else:
            spans.append((float(sg.start), float(sg.end)))
    return spans
