"""Saved transcripts on disk: one folder per recording ('Transcripts/<name>/'), each version's
.txt/.srt/.vtt plus side files (_speakers.json = the master list of lines, _words.json, _session.json,
_markers.json). Finding and grouping them, naming, reading and writing."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from transcriber.core import Segment, Transcript, format_timestamp, safe_label, write_text_safe
from transcriber.names import (SIDECAR_ENDS, TRACK_SUFFIX, VERSION_SUFFIX, base_name, own_number,
                               recording_folder, stem_of)
from transcriber.words import read_words, words_path, write_words


_TXT_NAME = re.compile(r"^(?P<stem>.*)_transcript(?P<dup> \(\d+\))?\.txt$", re.IGNORECASE)


def scan_transcripts(folder) -> list[dict]:
    """Group the transcripts in a folder by the video they came from.

    Returns newest first: [{"key": name, "files": {"txt": Path, "srt": Path, ...}, "mtime": float,
    "source": original media path or None}]. Only files this app writes are included.
    """
    groups: dict[tuple, dict] = {}
    entries = _entries(folder)
    if entries is None:
        return []
    for e in entries:
        if not e.is_file():
            continue
        name = e.name
        m = _TXT_NAME.match(name)
        if m:
            fmt, key = "txt", m.group("stem") + (m.group("dup") or "")
        elif name.lower().endswith("_speakers.json"):
            fmt, key = "speakers", name[: -len("_speakers.json")]
        elif name.lower().endswith("_words.json"):
            fmt, key = "words", name[: -len("_words.json")]
        elif name.lower().endswith("_session.json"):
            fmt, key = "session", name[: -len("_session.json")]
        elif name.lower().endswith((".srt", ".vtt")):
            fmt, key = name[-3:].lower(), name[:-4]
        else:
            continue
        # Grouped per folder too: "Interview (2)" can be a version in Interview's folder AND a
        # different recording (a copied file) in its own folder.
        g = groups.setdefault((os.path.dirname(e.path), key),
                              {"key": key, "files": {}, "mtime": 0.0, "source": None, "model": "", "track": ""})
        g["files"][fmt] = Path(e.path)
        try:
            g["mtime"] = max(g["mtime"], e.stat().st_mtime)
        except OSError:
            pass
    for g in groups.values():
        if g["files"].get("txt"):
            head = read_header(g["files"]["txt"])
            g["source"] = head.get("Transcript of") or None
            g["model"] = head.get("Model", "")
            g["track"] = head.get("Track", "")
    return sorted(groups.values(), key=lambda g: g["mtime"], reverse=True)


def group_versions(groups: list[dict]) -> list[dict]:
    """Combine a video's files into one transcript: 'Talk', 'Talk (2)', 'Talk [Mic]', 'Talk [Mic] (2)'...

    Returns newest first: [{"key": video name, "tracks": {"": [versions...], "Mic": [...]},
    "versions": versions of the first track (oldest ... newest), "mtime", "source"}].
    Track "" means all tracks mixed. Each version is a scan_transcripts() group.
    """
    by_base: dict[str, dict[str, list[dict]]] = {}
    sessions = {}
    for g in groups:
        if "session" in g["files"]:             # a combined recording's list of files, not a transcript
            sessions[g["key"]] = g["files"]["session"]
            g = dict(g, files={k: v for k, v in g["files"].items() if k != "session"})
            if not g["files"]:
                continue
        key = g["key"]
        m = VERSION_SUFFIX.match(key)
        if m and not own_number(key, g.get("source")):
            key = m.group("base")
        t = TRACK_SUFFIX.match(key)
        base, track = (t.group("base"), t.group("track")) if t else (key, "")
        if g.get("track"):
            track = g["track"]          # the header's name beats the (file-safe) one in the file name
        by_base.setdefault(base, {}).setdefault(track, []).append(g)
    out = []
    for base, tracks in by_base.items():
        for versions in tracks.values():
            versions.sort(key=lambda v: v["mtime"])
        ordered = dict(sorted(tracks.items(), key=lambda kv: (kv[0] != "", kv[0].lower())))  # mixed first
        everything = [v for vs in ordered.values() for v in vs]
        newest = max(everything, key=lambda v: v["mtime"])
        source = next((v["source"] for v in sorted(everything, key=lambda v: -v["mtime"]) if v["source"]), None)
        session = sessions.get(base)
        if session:                             # combined recording: play the video, or the first mic
            from transcriber.session import read_session, reference_file
            data = read_session(session)
            source = reference_file(data) or source if data else source
        out.append({"key": base, "tracks": ordered, "versions": next(iter(ordered.values())),
                    "mtime": newest["mtime"], "source": source, "session": session})
    return sorted(out, key=lambda t: t["mtime"], reverse=True)


def folder_signature(folder) -> tuple:
    """Cheap fingerprint of a folder's transcript files, to notice changes without rescanning."""
    try:
        return tuple(sorted(
            (e.path, e.stat().st_mtime) for e in (_entries(folder) or [])
            if e.is_file() and e.name.lower().endswith((".txt", ".srt", ".vtt"))
        ))
    except OSError:
        return ()


def _entries(folder):
    """The files in the transcripts folder and in its recording folders (one level down)."""
    try:
        top = list(os.scandir(folder))
    except OSError:
        return None
    out = []
    for e in top:
        if e.is_dir() and not e.name.startswith("."):
            try:
                out += [x for x in os.scandir(e.path) if x.is_file()]
            except OSError:
                pass
        elif e.is_file():
            out.append(e)
    return out


def organize_folder(folder) -> list:
    """Move transcript files sitting loose in the transcripts folder into a folder per recording.
    Backups move along so 'Earlier copies' still finds them. Returns [(old, new)]."""
    from transcriber import backups
    moved = []
    try:
        loose = [e for e in os.scandir(folder) if e.is_file()]
    except OSError:
        return moved
    own = set()          # stems whose " (2)" is the recording's own name, judged from the .txt header
    for e in loose:
        if e.name.lower().endswith("_transcript.txt"):
            stem = e.name[: -len("_transcript.txt")]
            if own_number(stem, read_header(e.path).get("Transcript of")):
                own.add(stem)
    for e in loose:
        stem = next((e.name[: -len(end)] for end in SIDECAR_ENDS if e.name.lower().endswith(end)), "")
        base = base_name(e.name, keep_number=stem in own)
        if not base:
            continue
        dest_dir = recording_folder(folder, base)
        dest = dest_dir / e.name
        if dest.exists() or (dest_dir.exists() and not dest_dir.is_dir()):
            continue
        old_bk = backups._folder_for({"f": Path(e.path)})
        try:
            dest_dir.mkdir(exist_ok=True)
            os.replace(e.path, dest)
        except OSError:
            continue      # open in another program: leave it where it is
        moved.append((Path(e.path), dest))
        new_bk = backups._folder_for({"f": dest})
        try:
            if old_bk and new_bk and old_bk.exists() and not new_bk.exists():
                old_bk.rename(new_bk)
        except OSError:
            pass
    return moved


_CUE = re.compile(r"^(\d+):(\d+):(\d+)[,.](\d+)\s+-->\s+(\d+):(\d+):(\d+)[,.](\d+)")


def read_cues(path) -> list:
    """The cues of a .srt/.vtt file: [(start, end, text)] with any <v Name> voice tag removed."""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    cues, k = [], 0
    while k < len(lines):
        m = _CUE.match(lines[k].strip())
        k += 1
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        text = []
        while k < len(lines) and lines[k].strip():
            text.append(re.sub(r"^<v [^>]*>", "", lines[k].strip()))
            k += 1
        cues.append((g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000,
                     g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000, " ".join(text)))
    return cues


def read_transcript_lines(path) -> list[tuple[str, str]]:
    """Return (timestamp, text) pairs from a saved .txt, .srt or .vtt file, for previewing."""
    path = Path(path)
    if path.suffix.lower() != ".txt":
        return [(format_timestamp(a), text) for a, _b, text in read_cues(path)]
    out: list[tuple[str, str]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        if line.startswith("Full text:"):
            break
        m = re.match(r"^\[(\d+:\d+:\d+)\]\s?(.*)$", line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def render_txt(t: Transcript) -> str:
    """Same layout as the original script: header, timestamped lines, then full text."""
    lines = [
        f"Transcript of: {t.source}",
        f"Model: {t.model}",
        f"Language: {t.language}",
        *([f"Track: {t.track}"] if t.track else []),
        "=" * 60,
        "",
        *[f"[{format_timestamp(s.start)}] " + (f"{t.name_at(i)}: " if t.name_at(i) else "") + s.text
          for i, s in enumerate(t.segments)],
        "",
        "=" * 60,
        "Full text:",
        "",
        t.text,
    ]
    return "\n".join(lines) + "\n"


def _cues(t: Transcript) -> list:
    """The transcript as captions: caption-sized pieces timed by the words (see captions.py), or
    one caption per line when that's turned off in Settings > Captions."""
    from transcriber import captions as C
    lines = [C.Line(s.start, s.end, s.text, t.name_at(i), list(s.words) or None, fixed=s.fixed, layer=s.layer)
             for i, s in enumerate(t.segments)]
    if not C.OPTIONS.get("split", True):
        many = len({ln.name for ln in lines if ln.name}) > 1
        return [C.Cue(ln.start, ln.end, [(f"{ln.name}: " if ln.name and many else "") + ln.text], ln.name)
                for ln in lines]
    return C.build(lines, sounds=True)


def render_srt(t: Transcript) -> str:
    from transcriber.captions import to_srt
    return to_srt(_cues(t))


def render_vtt(t: Transcript) -> str:
    from transcriber.captions import to_vtt
    return to_vtt(_cues(t))


WRITERS = {
    "txt": ("_transcript.txt", render_txt),
    "srt": (".srt", render_srt),
    "vtt": (".vtt", render_vtt),
}


def save_transcript(t: Transcript, output_dir: Path, formats: list[str], overwrite: bool = False,
                    name: Optional[str] = None) -> list[Path]:
    """Write the transcript in each format. Earlier versions are never overwritten: a new version
    gets the next free number, shared by all its formats ('Talk (2)_transcript.txt', 'Talk (2).srt')."""
    output_dir = recording_folder(output_dir, name or stem_of(t.source))
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = (name or stem_of(t.source)) + (f" [{safe_label(t.track)}]" if t.track else "")

    def names(tag):
        return {fmt: output_dir / f"{stem}{tag}{suffix}" for fmt, (suffix, _) in WRITERS.items()}

    tag, n = "", 2
    while not overwrite and any(p.exists() for p in names(tag).values()):
        tag, n = f" ({n})", n + 1
    paths = names(tag)
    saved = []
    for fmt in formats:
        write_text_safe(paths[fmt], WRITERS[fmt][1](t))
        saved.append(paths[fmt])
    # The master list of lines (and who spoke when, if known). Every edit is made here and the
    # .txt/.srt/.vtt are rebuilt from it, so the formats can never drift apart.
    from transcriber.speakers import speakers_path, write_speakers_file
    sp = speakers_path(paths["txt"])
    labels = t.speakers if len(t.speakers) == len(t.segments) else [None] * len(t.segments)
    write_speakers_file(sp, source=t.source, model=t.model, language=t.language, track=t.track,
                        segments=t.segments, labels=labels, voiceprints=t.voiceprints,
                        people_ids=t.people_ids)
    saved.append(sp)
    if any(seg.words for seg in t.segments):   # when each word was said, for exact splits and clicks
        wp = words_path(paths["txt"])
        write_words(wp, [seg.words for seg in t.segments])
        saved.append(wp)
    return saved


def read_version(files: dict):
    """A version's lines as Segments with exact times and WITHOUT names in front (the .txt/.srt
    put "Mel: " before each line; the speakers file keeps text and speaker apart), plus each line's
    speaker label and the speakers file's data. Word timings are attached when they line up."""
    data = None
    if files.get("speakers"):
        from transcriber.speakers import read_speakers_file
        data = read_speakers_file(files["speakers"])
    if data and data.get("segments"):
        segs = [Segment(float(x["start"]), float(x["end"]), x.get("text", ""), fixed=bool(x.get("fixed")),
                        layer=x.get("layer")) for x in data["segments"]]
        labels = [x.get("speaker") for x in data["segments"]]
    else:
        segs, labels, data = read_segments(files), [], None
    words = read_words(files["words"]) if files.get("words") else None
    if words and len(words) == len(segs):
        for sg, ws in zip(segs, words):
            sg.words = [tuple(w) for w in ws]
    return segs, labels, data


def read_header(path) -> dict:
    """The "Key: value" lines at the top of a saved .txt (Transcript of, Model, Language, Track)."""
    out = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for _ in range(6):
                k, sep, v = fh.readline().partition(":")
                if sep and k in ("Transcript of", "Model", "Language", "Track"):
                    out[k] = v.strip()
    except (OSError, TypeError):
        pass
    return out


def read_segments(files: dict) -> list[Segment]:
    """A saved version's lines with start and end times (from .srt/.vtt; a .txt only has starts,
    so each line is taken to last until the next one)."""
    for fmt in ("srt", "vtt"):
        segs = [Segment(a, b, text) for a, b, text in read_cues(files[fmt])] if files.get(fmt) else []
        if segs:
            return segs
    if files.get("txt"):
        starts = []
        for ts, text in read_transcript_lines(files["txt"]):
            h, m, sec = (int(x) for x in ts.split(":"))
            starts.append((h * 3600 + m * 60 + sec, text))
        return [Segment(st, starts[i + 1][0] if i + 1 < len(starts) else st + 5, text)
                for i, (st, text) in enumerate(starts)]
    return []
