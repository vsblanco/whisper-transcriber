"""Cutting a video from the transcript: keep only the marked parts, or leave out the deleted lines.

The pieces are plain arithmetic on (start, end) spans, kept apart from the window so they can be
tested: which stretches to keep, and where each caption lands once the gaps are gone."""

from __future__ import annotations

from dataclasses import replace
from typing import Optional


def merge_spans(spans: list, pad: float = 0.0, duration: Optional[float] = None, min_len: float = 0.2) -> list:
    """Sorted, non-overlapping (start, end) spans: each padded by `pad` on both sides, kept inside
    0..duration, overlapping or touching ones joined, slivers shorter than min_len dropped."""
    out: list = []
    for a, b in sorted((float(a), float(b)) for a, b in spans):
        a, b = max(a - pad, 0.0), b + pad
        if duration is not None:
            b = min(b, duration)
        if b - a < min_len:
            continue
        if out and a <= out[-1][1] + 0.001:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def keep_from_drop(drop: list, duration: float, min_len: float = 0.2) -> list:
    """What's left of 0..duration once the `drop` spans are taken out."""
    out, t = [], 0.0
    for a, b in merge_spans(drop, 0.0, duration, 0.0):
        if a - t >= min_len:
            out.append((t, a))
        t = max(t, b)
    if duration - t >= min_len:
        out.append((t, duration))
    return out


def total(spans: list) -> float:
    return sum(b - a for a, b in spans)


def remap_time(t: float, spans: list) -> Optional[float]:
    """Where a moment of the original lands in the cut video (None if it was cut out)."""
    at = 0.0
    for a, b in spans:
        if a <= t <= b:
            return at + (t - a)
        at += b - a
    return None


def remap_cues(cues: list, spans: list) -> list:
    """Captions moved onto the cut video's timeline: each clipped to the span it falls in (a
    caption across a cut is split, one piece per span), with the gaps removed."""
    out, at = [], 0.0
    for a, b in spans:
        for c in cues:
            if c.end <= a or c.start >= b:
                continue
            s, e = max(c.start, a), min(c.end, b)
            if e - s < 0.05:
                continue
            out.append(replace(c, start=at + (s - a), end=at + (e - a)))
        at += b - a
    out.sort(key=lambda c: (c.start, c.end))
    return out


def concat_list(paths: list) -> str:
    """The ffmpeg concat demuxer's list file."""
    def q(p: str) -> str:
        return "'" + str(p).replace("'", r"'\''") + "'"
    return "".join(f"file {q(p)}\n" for p in paths)


def concat_command(list_file: str, out: str, video: bool = True) -> list:
    """Join the parts (all encoded alike) without re-encoding."""
    return (["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy"]
            + (["-movflags", "+faststart"] if video else []) + [out])
