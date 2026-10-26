"""Changing saved transcripts: text edits, deleted lines, new times, splitting a line, and adding
speakers later. Every change goes through the version's master list of lines (its _speakers.json)
and the .txt/.srt/.vtt are rebuilt from it, so the formats always match."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Callable, Optional

from transcriber.core import Segment, Transcript, extract_audio, write_text_safe
from transcriber.files import WRITERS, read_header, read_segments, read_version
from transcriber.words import _update_words, read_words, write_words


def ensure_lines_file(files: dict) -> dict:
    """Older versions have no master list of lines: make one from their files (exact times from
    the subtitles when there are any) so edits go through it. Returns the files with it."""
    if files.get("speakers"):
        return files
    first = files.get("txt") or files.get("srt") or files.get("vtt")
    segs = read_segments(files) if first else []
    if not segs:
        return files
    from transcriber.speakers import speakers_path, write_speakers_file
    head = read_header(files["txt"]) if files.get("txt") else {}
    sp = speakers_path(first)
    write_speakers_file(sp, source=head.get("Transcript of", ""), model=head.get("Model", ""),
                        language=head.get("Language", ""), track=head.get("Track", ""), segments=segs,
                        labels=[None] * len(segs), voiceprints={}, people_ids={})
    return dict(files, speakers=sp)


def apply_text_edits(files: dict, edits: dict, people=None, deletes=(), times=None) -> None:
    """Save changes to one version: edits = {line index: new text}, deletes = line indexes to remove,
    times = {line index: new start in seconds}. Lines are put back in time order in the master list,
    then the .txt/.srt/.vtt and word timings are rebuilt to match."""
    edits = {int(i): " ".join(str(t).split()) for i, t in edits.items()}
    deletes = {int(i) for i in deletes}
    times = {int(i): max(float(t), 0.0) for i, t in (times or {}).items()}
    files.update(ensure_lines_file(files))
    data = _read_lines(files)
    if data is None:
        return
    segs = data["segments"]
    for i, text in edits.items():
        if 0 <= i < len(segs):
            segs[i]["text"] = text
    for i, start in times.items():
        if 0 <= i < len(segs):
            length = max(float(segs[i]["end"]) - float(segs[i]["start"]), 0.5)
            segs[i]["start"], segs[i]["end"] = round(start, 3), round(start + length, 3)
    starts = [float(x["start"]) for x in segs]
    kept = [(i, x) for i, x in enumerate(segs) if i not in deletes]
    data["segments"] = [x for _, x in sorted(kept, key=lambda p: (float(p[1]["start"]), p[0]))]
    _save_lines(files, data, people)
    _update_words(files, edits, deletes, times, lambda i: (starts[i], i))


def split_line(files: dict, i: int, left: str, right: str, start: float, new_start: float, people=None) -> None:
    """Split line i of a version into two: left text at `start`, right text at `new_start`.
    The new line goes right after it (callers pick new_start so the order stays the same)."""
    left, right = " ".join(left.split()), " ".join(right.split())
    files.update(ensure_lines_file(files))
    data = _read_lines(files)
    if data is None or not 0 <= i < len(data["segments"]):
        return
    wp = files.get("words")
    lines = read_words(wp) if wp else None
    if lines is not None and 0 <= i < len(lines):
        ws = lines[i]
        n = len(left.split())
        fits = " ".join(w[2].strip() for w in ws) == " ".join((left + " " + right).split())
        lines[i:i + 1] = [ws[:n], ws[n:]] if fits else [[], []]
        write_words(wp, lines)
    seg = data["segments"][i]
    end = max(float(seg["end"]), new_start + 0.5)
    second = dict(seg, text=right, start=round(new_start, 3), end=round(end, 3))
    seg.update(text=left, start=round(start, 3), end=round(new_start, 3))
    data["segments"].insert(i + 1, second)
    _save_lines(files, data, people)


def _read_lines(files: dict) -> Optional[dict]:
    from transcriber.speakers import read_speakers_file
    data = read_speakers_file(files["speakers"]) if files.get("speakers") else None
    return data if data and isinstance(data.get("segments"), list) else None


def _save_lines(files: dict, data: dict, people) -> None:
    """Write the master list, then rebuild the version's .txt/.srt/.vtt from it."""
    write_text_safe(files["speakers"], json.dumps(data))
    rewrite_from_speakers(files["speakers"], files, people)


def add_speakers(files: dict, source: str, track: Optional[int], track_name: str, token: str, people,
                 log: Callable[[str], None] = print):
    """Detect who's speaking in a transcript that was made without it, without transcribing again.
    Writes the speakers file and puts the names into the .txt/.srt/.vtt. Returns the speakers file."""
    from transcriber.speakers import assign, diarize, match_people, speakers_path, write_speakers_file
    if not source or not os.path.exists(source):
        raise FileNotFoundError(source or "the original file")
    segments, _labels, data = read_version(files)       # the master list: exact times, no names
    if not segments:
        raise RuntimeError("This transcript has no lines to label.")
    head = read_header(files["txt"]) if files.get("txt") else {}
    model = head.get("Model") or (data or {}).get("model", "")
    language = head.get("Language") or (data or {}).get("language", "")
    fd, wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        log("Extracting audio...")
        extract_audio(source, wav, track)
        turns, prints = diarize(wav, token, log)
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass
    if not turns:
        raise RuntimeError("No speech was found.")
    labels = assign(segments, turns)
    ids, _names = match_people(labels, prints, people)
    sp = files.get("speakers") or speakers_path(files.get("txt") or files.get("srt") or files.get("vtt"))
    write_speakers_file(sp, source=source, model=model, language=language, track=track_name,
                        segments=segments, labels=labels, voiceprints=prints, people_ids=ids)
    files = dict(files, speakers=sp)
    rewrite_from_speakers(sp, files, people)
    return sp


def rewrite_from_speakers(speakers_file, files: dict, people) -> None:
    """Rebuild a version's .txt/.srt/.vtt from its speakers file (after a name changes)."""
    from transcriber.speakers import display_names, read_speakers_file
    data = read_speakers_file(speakers_file)
    if not data:
        return
    segs = [Segment(x["start"], x["end"], x["text"]) for x in data["segments"]]
    t = Transcript(source=data.get("source", ""), model=data.get("model", ""), language=data.get("language", ""),
                   segments=segs, text=" ".join(x.text for x in segs), track=data.get("track", ""),
                   speakers=[x.get("speaker") for x in data["segments"]], names=display_names(data, people))
    for fmt in ("txt", "srt", "vtt"):
        if fmt in files:
            write_text_safe(files[fmt], WRITERS[fmt][1](t))


def set_spans(files: dict, spans: list, people=None) -> None:
    """Give every line of a version new (start, end) times, in the same order (e.g. lined up with
    the voice). Word timings are left as they are."""
    files.update(ensure_lines_file(files))
    data = _read_lines(files)
    if data is None or len(data["segments"]) != len(spans):
        raise RuntimeError("The transcript changed; try again.")
    for seg, (a, b) in zip(data["segments"], spans):
        seg["start"], seg["end"] = round(float(a), 3), round(float(b), 3)
    _save_lines(files, data, people)
