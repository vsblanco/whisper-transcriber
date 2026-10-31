"""Turning transcript lines into captions the way broadcasters and streaming services do.

A transcript line can be a long sentence; a caption is what fits on screen at once. The rules come
from the Netflix timed text guide, the BBC subtitle guidelines and the DCMP captioning key:

- at most 2 lines of 42 characters, and at most 7 seconds on screen
- at least 5/6 of a second on screen, and slow enough to read (at most 20 characters a second)
- lines break at natural places: after punctuation, before "and", "to", "in"...; never between
  "the" and its word, a first and last name, or a pronoun and its verb
- captions start when the first word is said (the word timings decide)
- a gap shorter than half a second between captions is closed (it only flickers), otherwise
  captions are 2 frames apart
- the speaker's name only when the speaker changes; two people talking at once get a dash each
- sounds as [laughs], [music]
- in and out times move onto a nearby scene cut, so a caption doesn't flash across a cut
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

MAX_CHARS = 42
MAX_LINES = 2
MAX_DUR = 7.0
MIN_DUR = 5 / 6
MAX_CPS = 20.0
MIN_GAP = 2 / 24          # 2 frames
CLOSE_GAP = 0.5           # a shorter gap is closed up
CUT_REACH = 0.5           # how close (seconds) a scene cut must be to move a caption onto it

# Settings > Captions (the app sets these): split into caption-sized pieces; line up with scene cuts.
OPTIONS = {"split": True, "cuts": True, "overlap": "layer"}   # overlap: "layer" (stacked) or "dash"
MAX_LAYERS = 3                     # people shown at once, stacked (any more wait their turn)

# Words a line shouldn't end on (they belong with the next word).
_KEEP_WITH_NEXT = {
    "a", "an", "the", "my", "your", "his", "her", "its", "our", "their", "this", "these", "those",
    "i", "we", "you", "they", "he", "she", "it's", "i'm", "i'll", "i've", "i'd", "we're", "you're",
    "they're", "not", "very", "so", "too", "mr.", "mrs.", "ms.", "dr.", "no.", "of", "to", "and", "or",
    "but", "if", "than", "as", "at", "in", "on", "for", "with", "from", "by", "about", "into",
}
# Good words to start a new line or caption with.
_START_WORDS = {
    "and", "but", "or", "so", "because", "that", "which", "who", "when", "where", "while", "if",
    "to", "of", "in", "on", "at", "for", "with", "from", "about", "like", "than", "then", "after",
    "before", "until", "since", "although", "though", "unless", "whether", "into", "through",
}


@dataclass
class Line:
    """One transcript line: its time, text, speaker and (when known) word timings."""
    start: float
    end: float
    text: str
    name: str = ""
    words: Optional[list] = None      # [(start, end, word)]
    track: str = ""
    color: str = ""
    fixed: bool = False               # start and end set by hand (the timeline): its captions keep them
    layer: Optional[int] = None       # a spot picked by hand on the timeline (0 = usual, 1, 2 = stacked above)
    src: object = None                # the caller's key for this line (its cues say which lines they came from)


@dataclass
class Cue:
    start: float
    end: float
    lines: list                       # 1 or 2 lines of text, as shown
    name: str = ""                    # who is talking ("" for a two-person dash caption)
    track: str = ""
    color: str = ""
    speech_end: float = 0.0
    show_name: bool = False           # put "Name:" in front (set when the speaker changes)
    names: list = field(default_factory=list)
    fixed: bool = False               # (from a fixed line: its time is kept as set)
    layer: int = 0                    # 0 at the usual spot; 1, 2 stacked above it (people talking at once)
    src: list = field(default_factory=list)   # Line.src of the line(s) this caption came from
    words: list = field(default_factory=list) # [(start, end, word)] of its words, in order (for word highlights)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


# ---------------------------------------------------------------- text

_SOUND_PAREN = re.compile(r"\(([A-Za-z][A-Za-z ,'-]{0,30})\)")
_SOUND_STAR = re.compile(r"\*([A-Za-z][A-Za-z ,'-]{0,30})\*")
_SOUND_BRACKET = re.compile(r"\[([^\]]{1,40})\]")


_TIDY_CACHE: dict = {}


def tidy_sounds(text: str) -> str:
    """Sound descriptions the standard way: '(Laughs)' or '*laughs*' -> '[laughs]'."""
    hit = _TIDY_CACHE.get(text)
    if hit is None:
        if len(_TIDY_CACHE) > 50000:
            _TIDY_CACHE.clear()
        hit = _TIDY_CACHE[text] = _tidy_sounds(text)
    return hit


def _tidy_sounds(text: str) -> str:
    text = _SOUND_PAREN.sub(lambda m: f"[{m.group(1).strip().lower()}]", text)
    text = _SOUND_STAR.sub(lambda m: f"[{m.group(1).strip().lower()}]", text)
    text = _SOUND_BRACKET.sub(lambda m: f"[{m.group(1).strip().lower()}]", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean(word: str) -> str:
    return word.strip().strip("\"“”‘’()[]").lower()


def break_cost(prev: str, nxt: str) -> float:
    """How bad it is to end a line or caption after `prev` (next word `nxt`). 0 = ideal.
    (Remembered per pair of words: a long transcript asks about the same pairs many times.)"""
    key = (prev, nxt)
    hit = _BREAK_CACHE.get(key)
    if hit is None:
        if len(_BREAK_CACHE) > 200000:
            _BREAK_CACHE.clear()
        hit = _BREAK_CACHE[key] = _break_cost(prev, nxt)
    return hit


_BREAK_CACHE: dict = {}


def _break_cost(prev: str, nxt: str) -> float:
    p, n = prev.strip(), nxt.strip()
    if not n:
        return 0.0
    if p.endswith((".", "?", "!", "…", '."', '?"', '!"', ".”", "?”", "!”")) and not p.lower() in ("mr.", "mrs.", "ms.", "dr."):
        return 0.0
    if p.endswith((",", ";", ":", "—", "-", "...")):
        return 1.0
    if n.startswith("[") or p.endswith("]"):
        return 1.0                     # around a sound tag
    pc, nc = _clean(p), _clean(n)
    if pc in _KEEP_WITH_NEXT:
        return 8.0
    if p[:1].isupper() and n[:1].isupper() and pc not in ("i",):
        return 6.0                     # probably a name: "Ian Burn"
    if p[:1].isdigit() and n[:1].isdigit():
        return 6.0
    if nc in _START_WORDS:
        return 2.0
    return 4.0


def wrap(words: list, max_chars: int = MAX_CHARS, max_lines: int = MAX_LINES):
    """Best way to show these words as lines: (cost, [line, ...]) or (None, None) if they don't fit."""
    total = sum(len(w) for w in words) + len(words) - 1
    if total <= max_chars:
        return 0.0, [" ".join(words)]
    if max_lines < 2:
        return None, None
    # the split point by lengths alone; the strings are joined just once, for the winner
    best, best_k, la = None, 0, -1
    for k in range(1, len(words)):
        la += len(words[k - 1]) + 1                    # length of words[:k] joined
        lb = total - la - 1
        if la > max_chars:
            break                                      # only gets longer
        if lb > max_chars:
            continue
        # a natural break first; then lines of similar length (the bottom one may be longer)
        cost = break_cost(words[k - 1], words[k]) * 1.5 + abs(la - lb) / 14 + (0.5 if la > lb + 8 else 0)
        if best is None or cost < best:
            best, best_k = cost, k
    if best is None:
        return None, None
    return best, [" ".join(words[:best_k]), " ".join(words[best_k:])]


# ---------------------------------------------------------------- word timings

_TIMED_CACHE: dict = {}


def _timed_words(line: Line) -> list:
    """[(start, end, word)] for the line's text: its word timings when they match the text, else
    times spread over the line by length. Kept inside the line's own start..end. (Remembered per
    line: captions are rebuilt after every edit, and the other lines are as they were.)"""
    try:
        key = (line.text, line.start, line.end, tuple(line.words) if line.words else None)
        hash(key)
    except TypeError:                               # words as lists (not from the app): just compute
        return _timed_words_calc(line)
    hit = _TIMED_CACHE.get(key)
    if hit is None:
        if len(_TIMED_CACHE) > 30000:
            _TIMED_CACHE.clear()
        hit = _TIMED_CACHE[key] = _timed_words_calc(line)
    return hit


def _timed_words_calc(line: Line) -> list:
    tokens = line.text.split()
    if not tokens:
        return []
    ws = [w for w in (line.words or []) if str(w[2]).strip()]
    a0, b0 = float(line.start), max(float(line.end), float(line.start) + 0.2)
    if len(ws) == len(tokens):
        out, last = [], a0
        for (ws_, we_, _w), tok in zip(ws, tokens):
            s = min(max(float(ws_), last, a0), b0)
            e = min(max(float(we_), s + 0.05), b0)
            out.append((s, e, tok))
            last = s
        return out
    total = sum(len(t) + 1 for t in tokens)
    out, pos = [], 0
    for tok in tokens:
        s = a0 + (b0 - a0) * pos / total
        pos += len(tok) + 1
        out.append((s, a0 + (b0 - a0) * pos / total, tok))
    return out


# ---------------------------------------------------------------- splitting

_SPLIT_CACHE: dict = {}


def _split_line(words: list, prefix: str, max_chars: int, max_lines: int, max_dur: float) -> list:
    """Cut one line's timed words into caption-sized groups: [(i, j, wrapped lines)]. Remembered
    per line: captions are rebuilt after every edit, and all the other lines are as they were."""
    key = (tuple(words), prefix, max_chars, max_lines, max_dur)
    hit = _SPLIT_CACHE.get(key)
    if hit is None:
        if len(_SPLIT_CACHE) > 30000:
            _SPLIT_CACHE.clear()
        hit = _SPLIT_CACHE[key] = _split_line_calc(words, prefix, max_chars, max_lines, max_dur)
    return hit


def _split_line_calc(words: list, prefix: str, max_chars: int, max_lines: int, max_dur: float) -> list:
    n = len(words)
    toks = [w[2] for w in words]
    INF = float("inf")
    best = [INF] * (n + 1)
    back = [0] * (n + 1)
    lines_for: dict = {}
    best[0] = 0.0
    lens = [len(t) for t in toks]
    cum = [0]                                            # cum[k] = length of toks[:k] joined with spaces
    for ln in lens:
        cum.append(cum[-1] + ln + 1)
    too_long = max_chars * max_lines + 8
    for j in range(1, n + 1):
        for i in range(j - 1, -1, -1):
            chars = cum[j] - cum[i] - 1 + (len(prefix) if (i == 0 and prefix) else 0)
            if chars > too_long and j - i > 1:
                break                                    # only gets longer going back
            group = ([prefix + toks[i]] if (i == 0 and prefix) else [toks[i]]) + toks[i + 1:j]
            cost, wrapped = wrap(group, max_chars, max_lines)
            dur = words[j - 1][1] - words[i][0]
            if wrapped is None or (dur > max_dur and j - i > 1):
                if j - i == 1:                           # a single huge word still has to go somewhere
                    cost, wrapped = 0.0, [" ".join(group)]
                else:
                    continue
            c = best[i] + 2.5 + cost                     # fewer captions read better
            if j < n:
                c += break_cost(toks[j - 1], toks[j]) * 1.5
                if words[j][0] - words[j - 1][1] > 0.3:  # a pause is a good place to cut
                    c -= 1.0
            if chars < 24 and n > j - i:
                c += (24 - chars) / 3                    # avoid short leftovers
            if dur < MIN_DUR and n > j - i:
                c += 1.0
            if c < best[j]:
                best[j], back[j] = c, i
                lines_for[(i, j)] = wrapped
    out, j = [], n
    while j > 0:
        i = back[j]
        out.append((i, j, lines_for[(i, j)]))
        j = i
    return out[::-1]


def _overlap(a: Cue, b: Cue) -> float:
    return max(0.0, min(a.end, b.end) - max(a.start, b.start))


def build(lines: list, *, max_chars: int = MAX_CHARS, max_lines: int = MAX_LINES, max_dur: float = MAX_DUR,
          min_dur: float = MIN_DUR, max_cps: float = MAX_CPS, min_gap: float = MIN_GAP,
          close_gap: float = CLOSE_GAP, names: str = "change", cuts: Optional[list] = None,
          sounds: bool = True) -> list:
    """Captions for transcript lines (any order, several speakers or tracks). names: "change" (a
    name only when the speaker changes, and only with 2+ speakers), "all" (every caption), "off"."""
    speakers = {ln.name for ln in lines if ln.name}
    many = len(speakers) > 1
    cues: list[Cue] = []
    timed: list = []                       # (line, its timed words), sentences Whisper cut in two joined
    last: dict = {}
    for ln in sorted(lines, key=lambda x: (x.start, x.end)):
        text = tidy_sounds(ln.text) if sounds else re.sub(r"\s+", " ", ln.text).strip()
        if not text:
            continue
        ln = Line(ln.start, ln.end, text, ln.name, ln.words, ln.track, ln.color, ln.fixed, ln.layer, ln.src)
        words = _timed_words(ln)
        key = (ln.name, ln.track)
        prev = last.get(key)
        if prev is not None and timed[prev][0] is not None and not ln.fixed and not timed[prev][0].fixed \
                and ln.layer is None and timed[prev][0].layer is None:
            pl, pw = timed[prev]
            if (0 <= ln.start - pl.end < 0.6 and not pl.text.rstrip().endswith((".", "?", "!", "…", '"', "”"))
                    and prev == len(timed) - 1 and words[-1][1] - pw[0][0] <= max_dur * 3):
                timed[prev] = (Line(pl.start, ln.end, f"{pl.text} {text}", pl.name, None, pl.track, pl.color,
                                    src=[*(pl.src if isinstance(pl.src, list) else [pl.src]), ln.src]),
                               pw + words)
                continue
        last[key] = len(timed)
        timed.append((ln, words))
    for ln, words in timed:
        # Room for "Name: " in case it's shown (decided later, once the order is known).
        prefix = f"{ln.name}: " if ln.name and names != "off" and (many or names == "all") else ""
        for i, j, wrapped in _split_line(words, prefix, max_chars, max_lines, max_dur):
            if i == 0 and prefix and wrapped[0].startswith(prefix):
                wrapped = [wrapped[0][len(prefix):]] + wrapped[1:]
            cue = Cue(words[i][0], words[j - 1][1], wrapped, ln.name, ln.track, ln.color,
                      speech_end=words[j - 1][1], names=[ln.name] if ln.name else [],
                      layer=ln.layer if ln.layer is not None else 0,
                      src=[x for x in (ln.src if isinstance(ln.src, list) else [ln.src]) if x is not None],
                      words=list(words[i:j]))
            if ln.layer is not None:
                cue.fixed_layer = True
            if ln.fixed:                       # set by hand: the first piece starts and the last ends on it
                cue.fixed = True
                if i == 0:
                    cue.start = ln.start
                if j == len(words):
                    cue.end = cue.speech_end = max(ln.end, cue.start + 0.2)
            cues.append(cue)
    cues.sort(key=lambda c: (c.start, c.end))

    # Two people at once (separate mics): one caption with a dash for each, when both fit (the
    # "dash" way; with "layer", each keeps its own caption and they stack on screen, see _layer).
    merged, skip = [], set()
    dash = OPTIONS.get("overlap", "layer") == "dash"
    for k, c in enumerate(cues):
        if k in skip:
            continue
        for m in range(k + 1, min(k + 4, len(cues))):
            d = cues[m]
            if m in skip or d.start >= c.end:
                break
            if c.fixed or d.fixed or not dash or getattr(c, "fixed_layer", False) or getattr(d, "fixed_layer", False):
                continue
            if (d.name != c.name or d.track != c.track) and len(c.lines) == 1 and len(d.lines) == 1 \
                    and len(c.lines[0]) <= max_chars - 2 and len(d.lines[0]) <= max_chars - 2 \
                    and _overlap(c, d) >= 0.5 * min(c.end - c.start, d.end - d.start) \
                    and max(c.end, d.end) - c.start <= max_dur:
                c = Cue(c.start, max(c.end, d.end), [f"- {c.lines[0]}", f"- {d.lines[0]}"], "", c.track, "",
                        speech_end=max(c.speech_end, d.speech_end), names=c.names + d.names, src=c.src + d.src,
                        words=c.words + d.words)
                skip.add(m)
                break
        merged.append(c)
    cues = merged

    # Who is talking: a name when it changes (dash captions already show a change).
    prev = None
    for c in cues:
        if c.name and names != "off" and (names == "all" or (many and c.name != prev)):
            c.show_name = True
        if c.names:
            prev = c.names[-1]
    for c in cues:
        if c.show_name:
            c.lines = [f"{c.name}: {c.lines[0]}"] + c.lines[1:]
            if any(len(x) > max_chars for x in c.lines):      # re-wrap with the name in
                _cost, wrapped = wrap(" ".join(c.lines).split(), max_chars, max_lines)
                if wrapped:
                    c.lines = wrapped

    _retime(cues, max_dur=max_dur, min_dur=min_dur, max_cps=max_cps, min_gap=min_gap, close_gap=close_gap,
            cuts=sorted(cuts or []))
    _layer(cues)
    return cues


def _layer(cues: list) -> None:
    """People talking at once: each caption its own spot on screen. The one that started first
    keeps the usual spot (layer 0); another person's caption that overlaps it goes above (1, then
    2). A spot picked by hand on the timeline is kept. Overlaps past MAX_LAYERS wait their turn."""
    order = sorted(range(len(cues)), key=lambda k: (cues[k].start, cues[k].end))
    busy: list = []                          # (end, layer, name, track) of captions still on screen
    for k in order:
        c = cues[k]
        busy = [b for b in busy if b[0] > c.start + 0.01]
        taken = {b[1] for b in busy if (b[2], b[3]) != (c.name, c.track)}
        if getattr(c, "fixed_layer", False):
            layer = c.layer
        else:
            same = next((b[1] for b in busy if (b[2], b[3]) == (c.name, c.track)), None)
            if same is not None and same not in taken:
                layer = same                 # the same person keeps their spot
            else:
                layer = next((n for n in range(MAX_LAYERS) if n not in taken), None)
            if layer is None:                # no free spot: after the others
                c.start = min(b[0] for b in busy) + 0.05
                c.end = max(c.end, c.start + MIN_DUR)
                busy = [b for b in busy if b[0] > c.start + 0.01]
                taken = {b[1] for b in busy if (b[2], b[3]) != (c.name, c.track)}
                layer = next((n for n in range(MAX_LAYERS) if n not in taken), 0)
            c.layer = layer
        busy.append((c.end, c.layer, c.name, c.track))


def _retime(cues: list, *, max_dur, min_dur, max_cps, min_gap, close_gap, cuts) -> None:
    """Reading time, minimum time, gaps and scene cuts, in time order."""
    import bisect

    def near_cut(t, before, after):
        if not cuts:
            return None
        i = bisect.bisect_left(cuts, t - before)
        best = None
        while i < len(cuts) and cuts[i] <= t + after:
            if best is None or abs(cuts[i] - t) < abs(best - t):
                best = cuts[i]
            i += 1
        return best

    for k, c in enumerate(cues):
        if c.fixed:
            continue
        # In time onto a scene cut just before (or a hair after) the first word.
        cut = near_cut(c.start, CUT_REACH, 0.25)
        prev_end = cues[k - 1].end if k else -1.0
        if cut is not None and cut >= prev_end + min_gap:
            c.start = max(cut, 0.0)
    layered = OPTIONS.get("overlap", "layer") != "dash"
    n = len(cues)
    for k, c in enumerate(cues):
        # the next caption this one has to make room for: with layering, only the same person's
        # (another person's caption gets its own spot on screen)
        nxt = None
        for m in range(k + 1, n):                  # (no list slices: thousands of captions)
            d = cues[m]
            if d.start > c.start + 0.01 and (not layered or (d.name, d.track) == (c.name, c.track)):
                nxt = d
                break
        if c.fixed:                            # as set by hand (only kept clear of the next caption)
            if nxt is not None and nxt.start < c.end and (nxt.name == c.name and nxt.track == c.track):
                c.end = max(nxt.start - min_gap, c.start + 0.3)
            continue
        overlapping = nxt is not None and nxt.start < c.speech_end - 0.05 and (nxt.name != c.name or nxt.track != c.track)
        limit = float("inf") if nxt is None or overlapping else nxt.start - min_gap
        chars = sum(len(x) for x in c.lines)
        need = max(min_dur, chars / max_cps)
        end = max(c.end, min(c.start + need, c.speech_end + 1.5))
        if c.end - c.start < min_dur:
            end = max(end, c.start + min_dur)
        cut = near_cut(end, 0.25, CUT_REACH)
        if cut is not None and cut - min_gap > c.start + min_dur and cut >= c.speech_end - 0.1:
            end = cut - min_gap
        if nxt is not None and not overlapping and 0 < nxt.start - end < close_gap:
            end = nxt.start - min_gap                           # close a flicker-sized gap
        end = min(end, limit, c.start + max_dur)
        c.end = max(end, c.start + 0.3)


# ---------------------------------------------------------------- files

def _ts(x: float, sep: str = ",") -> str:
    ms = int(round(max(x, 0) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}{sep}{ms % 1000:03d}"


def to_srt(cues: list, offset: float = 0.0) -> str:
    """SRT can't place captions, so people talking at once share one caption with a dash each."""
    out = []
    for i, c in enumerate(flatten_layers(cues), 1):
        out.append(f"{i}\n{_ts(c.start - offset)} --> {_ts(c.end - offset)}\n{c.text}\n")
    return "\n".join(out)


def flatten_layers(cues: list) -> list:
    """For formats with one spot on screen: stacked captions (people at once) folded into dash
    captions where they overlap, like the "dash" setting makes."""
    if not any(c.layer for c in cues):
        return cues
    out, used = [], set()
    order = sorted(range(len(cues)), key=lambda k: (cues[k].start, cues[k].layer))
    for k in order:
        if k in used:
            continue
        c = cues[k]
        if c.layer:                                 # (a stacked one with nothing under it: as it is)
            out.append(c)
            continue
        above = [m for m in order if m != k and m not in used and cues[m].layer and cues[m].start < c.end
                 and cues[m].end > c.start]
        if above:
            lines = [f"- {c.text.replace(chr(10), ' ')}"] + [f"- {cues[m].text.replace(chr(10), ' ')}" for m in above[:1]]
            out.append(Cue(min(c.start, cues[above[0]].start), max(c.end, cues[above[0]].end), lines, "", c.track, "",
                           speech_end=max(c.speech_end, cues[above[0]].speech_end), names=c.names + cues[above[0]].names,
                           src=c.src + cues[above[0]].src, words=c.words + cues[above[0]].words))
            used.add(above[0])
        else:
            out.append(c)
    out.sort(key=lambda c: (c.start, c.end))
    return out


# where each stacked spot sits (from the bottom, in lines of caption text)
LAYER_LINES = (0, 2.6, 5.2)


def to_vtt(cues: list, offset: float = 0.0) -> str:
    out = ["WEBVTT\n"]
    for c in cues:
        text = c.text
        if c.name:                                  # tags the speaker (players can color it)
            text = f"<v {c.name}>{text}</v>"
        where = ""
        if c.layer:                                 # stacked above the usual spot (players that can)
            where = f" line:{int(100 - 8 - c.layer * 12)}%"
        out.append(f"{_ts(c.start - offset, '.')} --> {_ts(c.end - offset, '.')}{where}\n{text}\n")
    return "\n".join(out)


def between(cues: list, a: float, b: float) -> list:
    """The captions that show during a..b, cut to it."""
    from dataclasses import replace
    return [replace(c, start=max(c.start, a), end=min(c.end, b)) for c in cues if c.end > a and c.start < b]


# ---------------------------------------------------------------- styled captions (ASS)

SPEAKER_STYLES = ("name", "text", "box")      # speaker's color on: the name, the whole text, the box behind it


def contrast_text(color: str) -> str:
    """White or near-black, whichever reads better on this color."""
    c = color.lstrip("#")
    try:
        r, g, b = (int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return "#ffffff"
    lum = 0.2126 * r ** 2.2 + 0.7152 * g ** 2.2 + 0.0722 * b ** 2.2
    return "#111111" if lum > 0.36 else "#ffffff"


def _word_pieces(c: Cue, lines: list, render, offset: float) -> Optional[list]:
    """For word highlights: (start, end, body) per word of the caption, the word wrapped in the
    highlight tag, times from the clip's start. render(lines) makes the body text from line strings.
    None when the caption's words can't be matched to its text (no word timings, or a name that
    doesn't fit)."""
    if not c.words:
        return None
    toks = [line.split(" ") for line in lines]
    spots = [(li, ti) for li, line in enumerate(toks) for ti, t in enumerate(line) if t and t != "-"]
    if c.show_name and c.name and lines and lines[0].startswith(f"{c.name}:"):
        spots = spots[len(c.name.split()):]            # the name in front isn't a spoken word
    if len(spots) != len(c.words):
        return None
    out, n = [], len(c.words)
    for k, (li, ti) in enumerate(spots):
        a = c.start if k == 0 else max(c.start, float(c.words[k][0]))
        b = float(c.words[k + 1][0]) if k + 1 < n else c.end
        b = min(max(b, a + 0.05), c.end)
        if b <= a:
            continue
        marked = [list(line) for line in toks]
        marked[li][ti] = "\x00" + marked[li][ti] + "\x01"            # placeholders: colored after rendering
        out.append((a - offset, b - offset, render([" ".join(line) for line in marked])))
    return out or None


def _ass_color(color: str, alpha: int = 0) -> str:
    c = (color or "#ffffff").lstrip("#")
    if len(c) != 6:
        c = "ffffff"
    return f"&H{alpha:02X}{c[4:6]}{c[2:4]}{c[0:2]}".upper()


def _ass_time(x: float) -> str:
    cs = int(round(max(x, 0) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


HIGHLIGHT = "#ffe14d"            # the word being said, when the speaker's color can't be used


def to_ass(cues: list, *, font: str = "Arial", size: float = 0.055, color: str = "#ffffff",
           background: str = "box", position: str = "bottom", speaker: str = "name",
           names: bool = True, add_names: bool = False, offset: float = 0.0,
           highlight: Optional[str] = None) -> str:
    """Captions as an ASS subtitle file, so a player or ffmpeg draws them with these looks:
    background "box" (see-through), "solid", "outline" or "shadow"; speaker "name" (the name in the
    speaker's color), "text" (all the text in it) or "box" (a box in it behind the text).
    add_names: put "Name:" in front of each caption that has a speaker (when the cues don't already).
    highlight: color each word as it's said ("speaker": in the speaker's color, or a "#rrggbb")."""
    h = 1080
    fs = max(int(h * size), 12)
    styles: dict = {}

    def style_for(text_color: str, box_color: Optional[str]) -> str:
        key = (text_color, box_color, background if box_color is None else "speaker")
        if key not in styles:
            if box_color is not None:
                border, outline, shadow, back, alpha = 3, 8, 0, box_color, 0x10
            elif background in ("box", "solid"):
                border, outline, shadow, back, alpha = 3, 8, 0, "#000000", 0x66 if background == "box" else 0x00
            elif background == "outline":
                border, outline, shadow, back, alpha = 1, 3, 0, "#000000", 0x00
            else:                                          # shadow
                border, outline, shadow, back, alpha = 1, 0, 3, "#000000", 0x40
            styles[key] = (f"S{len(styles)}", text_color, back, alpha, border, outline, shadow)
        return styles[key][0]

    events = []
    for c in cues:
        spk = c.color or ""
        text_color = color
        box = None
        if spk and speaker == "text":
            text_color = spk
        elif spk and speaker == "box":
            box, text_color = spk, contrast_text(spk)
        lines = [x.replace("{", "(").replace("}", ")") for x in c.lines]
        name = c.name if (add_names and names and c.name) else ""

        def render(lines, c=c, name=name, spk=spk):
            body = r"\N".join(lines)
            if name:
                if speaker == "name" and spk:
                    body = "{\\c" + _ass_color(spk).replace("&H00", "&H") + "&}" + name + ":{\\r}  " + body
                else:
                    body = f"{name}:  {body}"
            elif speaker == "name" and spk and not add_names and c.show_name and lines and lines[0].startswith(f"{c.name}:"):
                # the cue already starts with "Name:" (caption files): color just the name
                rest = body[len(c.name) + 1:]
                body = "{\\c" + _ass_color(spk).replace("&H00", "&H") + "&}" + c.name + ":{\\r}" + rest
            return body
        body = render(lines)
        margin = int(h * 0.05 + c.layer * fs * 2.6) if c.layer else 0      # stacked: its own height
        head = f"Dialogue: {c.layer},"
        tail = f",{style_for(text_color, box)},,0,0,{margin},,"
        pieces = _word_pieces(c, lines, render, offset) if highlight else None
        if pieces:
            if highlight == "speaker":
                hl = spk if (spk and text_color != spk and speaker == "name") else HIGHLIGHT
            else:
                hl = highlight
            tag = "{\\c" + _ass_color(hl).replace("&H00", "&H") + "&}"
            for a, b, text in pieces:
                text = text.replace("\x00", tag).replace("\x01", "{\\r}")
                events.append(f"{head}{_ass_time(a)},{_ass_time(b)}{tail}{text}")
        else:
            events.append(f"{head}{_ass_time(c.start - offset)},{_ass_time(c.end - offset)}{tail}{body}")
    align = 8 if position == "top" else 2
    out = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1920", f"PlayResY: {h}", "WrapStyle: 0",
           "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
           "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
           "Alignment, MarginL, MarginR, MarginV, Encoding"]
    if not styles:
        style_for(color, None)
    for name, tc, back, alpha, border, outline, shadow in styles.values():
        box = _ass_color(back, alpha)
        out.append(f"Style: {name},{font},{fs},{_ass_color(tc)},{_ass_color(tc)},{box},{box},-1,0,0,0,100,100,0,0,"
                   f"{border},{outline},{shadow},{align},60,60,{int(h * 0.05)},1")
    out += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    return "\n".join(out + events) + "\n"
