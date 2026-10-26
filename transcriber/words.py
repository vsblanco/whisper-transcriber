"""Word timings: '<name>_words.json' next to a transcript, one list of [start, end, word] per line.

Also: finding words Whisper made up (no sound under them), and borrowing word times from another
version of the same audio when a line was edited or a version has none."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from transcriber.core import write_text_safe


def words_path(first_file) -> Path:
    from transcriber.names import sibling
    return sibling(first_file, "words")


def write_words(path, lines: list) -> None:
    data = {"version": 1, "lines": [[[round(a, 3), round(b, 3), w] for a, b, w in ws] for ws in lines]}
    write_text_safe(path, json.dumps(data))


def read_words(path) -> Optional[list]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")).get("lines")
    except (OSError, ValueError, AttributeError):
        return None


def word_at(words: list, text: str, offset: int):
    """The [start, end, word] under character `offset` of a line's text, matching the words in
    order. None if the words don't fit the text (it was edited)."""
    pos = 0
    low = text.lower()
    for ws in words:
        w = ws[2].strip().lower()
        if not w:
            continue
        k = low.find(w, pos)
        if k < 0:
            return None
        if offset < k + len(w):
            return ws
        pos = k + len(w)
    return words[-1] if words else None


def word_spans(text: str, words: list) -> list:
    """[(char start, char end, word)] for each word of `text`; [] if they don't fit (edited)."""
    out, pos, low = [], 0, text.lower()
    for ws in words:
        w = ws[2].strip().lower()
        if not w:
            continue
        k = low.find(w, pos)
        if k < 0:
            return []
        pos = k + len(w)
        out.append((k, pos, ws))
    return out


MADE_UP = 0.02          # a recognized word shorter than this has no sound under it


MADE_UP_RUN = 3         # this many such words in a row = text Whisper made up


def suspect_spans(text: str, words: list) -> list:
    """Character ranges of `text` that Whisper probably made up: runs of words with no sound under
    them (zero length). [] if the words don't fit the text (it was edited)."""
    spans, run, pos = [], [], 0
    low = text.lower()

    def flush():
        if len(run) >= MADE_UP_RUN:
            spans.append((run[0][0], run[-1][1]))
        run.clear()
    for a, b, w in words:
        w = w.strip().lower()
        if not w:
            continue
        k = low.find(w, pos)
        if k < 0:
            return []
        pos = k + len(w)
        if b - a < MADE_UP:
            run.append((k, pos))
        else:
            flush()
    flush()
    return spans


def remove_spans(text: str, spans: list) -> str:
    for a, b in sorted(spans, reverse=True):
        text = text[:a] + " " + text[b:]
    return re.sub(r"\s+([,.!?;:])", r"\1", " ".join(text.split())).strip()


def _norm_word(w: str) -> str:
    return re.sub(r"[^\w']", "", w.lower())


def align_words(text: str, pool: list, start: Optional[float] = None, end: Optional[float] = None,
                min_match: float = 0.3) -> list:
    """Word timings for `text`, borrowed from `pool` (the words of an earlier version of the same
    stretch of audio): matching words keep their times, the rest are spread between their
    neighbors. [] when too few words match to be trusted."""
    import difflib
    toks = text.split()
    if not toks:
        return []
    pool = [w for w in pool if w[1] - w[0] >= MADE_UP]          # never borrow from made-up words
    a = [_norm_word(t) for t in toks]
    b = [_norm_word(w[2]) for w in pool]
    times: list = [None] * len(toks)
    matched = 0
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            if a[blk.a + k]:
                times[blk.a + k] = (pool[blk.b + k][0], pool[blk.b + k][1])
                matched += 1
    if matched < max(1, min_match * len(toks)):
        return []
    lo = start if start is not None else (pool[0][0] if pool else 0.0)
    hi = end if end is not None else (pool[-1][1] if pool else lo + 0.3 * len(toks))
    i = 0
    while i < len(toks):                         # spread unmatched runs over the gap around them
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(toks) and times[j] is None:
            j += 1
        g0 = times[i - 1][1] if i > 0 else min(lo, times[j][0]) if j < len(toks) else lo
        g1 = times[j][0] if j < len(toks) else max(hi, g0)
        g1 = max(g1, g0)
        step = (g1 - g0) / (j - i)
        for k in range(i, j):
            times[k] = (g0 + step * (k - i), g0 + step * (k - i + 1))
        i = j
    return [[round(t0, 3), round(max(t1, t0 + 0.05), 3), " " + tok] for tok, (t0, t1) in zip(toks, times)]


def retime_lines(rows: list, pool: list) -> list:
    """rows = [(start, end, text)] -> word timings per row, borrowed from `pool` (all words of
    other versions of the same audio, any order)."""
    import bisect
    pool = sorted(pool, key=lambda w: w[0])
    starts = [w[0] for w in pool]
    out = []
    for start, end, text in rows:
        near = pool[bisect.bisect_left(starts, start - 1.5): bisect.bisect_left(starts, end + 1.5)]
        out.append(align_words(text, near, start, end))
    return out


def _update_words(files: dict, edits: dict, deletes: set, times: dict, order_key) -> None:
    """Keep the word timings lined up after edits: edited lines keep the times of their remaining
    words, moved lines shift, deleted lines go, and everything is put in the same order."""
    wp = files.get("words")
    lines = read_words(wp) if wp else None
    if lines is None:
        return
    rows = []
    for i, ws in enumerate(lines):
        if i in deletes:
            continue
        if i in edits:                  # keep the times of the words that are still there
            ws = align_words(edits[i], ws, ws[0][0], ws[-1][1]) if ws else []
        if i in times and ws:
            d = times[i] - ws[0][0]
            ws = [[a + d, b + d, w] for a, b, w in ws]
        rows.append((order_key(i), ws))
    rows.sort(key=lambda r: r[0])
    write_words(wp, [ws for _, ws in rows])
