"""Caption quality checks: what a viewer would trip over, found before the file goes out.

Works on the built captions (what the player, caption files and clips show) and on the transcript
lines behind them. Each problem: {"kind", "level" ("bad" | "warn"), "start", "end", "text", "src",
"detail"}; src is the key of the transcript line it came from (when known), so a click can go there."""

from __future__ import annotations

import re

from transcriber.captions import MAX_CHARS, MAX_CPS, MIN_DUR

FAST = MAX_CPS            # characters per second a viewer can still read
VERY_FAST = MAX_CPS * 1.3
SHORT = MIN_DUR           # up on screen for less than this: a flicker
LONG = 8.0                # up for longer than this: it's probably hanging around
LONG_LINE = 300           # a transcript line this long is usually Whisper running on
_REPEAT = re.compile(r"\b(\w+(?:\s+\w+)?)(?:[\s,]+\1\b){2,}", re.IGNORECASE)   # "and and and", "I know I know I know"


def _one(kind, level, c, detail, src=None):
    return {"kind": kind, "level": level, "start": float(c.start), "end": float(c.end),
            "text": c.text.replace("\n", " "), "src": src if src is not None else (c.src[0] if c.src else None),
            "detail": detail}


def check_cues(cues: list) -> list:
    """Problems in the captions as they'd be shown."""
    out = []
    by_spot: dict = {}
    for c in sorted(cues, key=lambda c: (c.start, c.end)):
        dur = max(c.end - c.start, 0.001)
        chars = sum(len(x) for x in c.lines)
        cps = chars / dur
        if cps > VERY_FAST:
            out.append(_one("Reads too fast", "bad", c, f"{cps:.0f} characters a second (over {FAST:.0f} is hard to read)"))
        elif cps > FAST * 1.1:
            out.append(_one("Reads fast", "warn", c, f"{cps:.0f} characters a second"))
        if dur < SHORT - 0.01:
            out.append(_one("Too short", "bad", c, f"on screen {dur:.2f} s (under {SHORT:.2f} s flickers)"))
        elif dur > LONG:
            out.append(_one("Up too long", "warn", c, f"on screen {dur:.1f} s"))
        for line in c.lines:
            if len(line) > MAX_CHARS + 2:
                out.append(_one("Line too long", "warn", c, f"{len(line)} characters on one line (the usual most is {MAX_CHARS})"))
                break
        if len(c.lines) > 2:
            out.append(_one("Too many lines", "warn", c, f"{len(c.lines)} lines"))
        key = (c.layer, c.track)
        prev = by_spot.get(key)
        if prev is not None and c.start < prev.end - 0.01:
            out.append(_one("Overlaps the one before", "bad", c,
                            f"starts {prev.end - c.start:.2f} s before the previous caption in the same spot ends"))
        if prev is None or c.end > prev.end:
            by_spot[key] = c
    return out


def check_lines(lines: list) -> list:
    """Problems in the transcript lines: [(key, start, end, text)]."""
    out = []
    for key, start, end, text in lines:
        text = " ".join((text or "").split())
        if not text:
            continue
        m = _REPEAT.search(text)
        if m:
            out.append({"kind": "Repeated words", "level": "warn", "start": start, "end": end, "text": text,
                        "src": key, "detail": f"“{m.group(1)}” over and over (Whisper stuck in a loop?)"})
        if len(text) > LONG_LINE:
            out.append({"kind": "Very long line", "level": "warn", "start": start, "end": end, "text": text,
                        "src": key, "detail": f"{len(text)} characters in one line"})
    return out


def check_all(cues: list, lines: list) -> list:
    """Everything, in time order, the worse first at the same moment."""
    out = check_cues(cues) + check_lines(lines)
    out.sort(key=lambda p: (p["start"], 0 if p["level"] == "bad" else 1))
    return out
