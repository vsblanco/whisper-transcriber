"""Speaker detection ("who spoke when") with pyannote, plus the speakers file saved next to each
transcript so names can be fixed later without transcribing again.

Needs:  pip install pyannote.audio   and a free Hugging Face token with access to
        pyannote/speaker-diarization-community-1 (accept its terms on the model page once).
The model downloads once (that's the only time the token is needed); after that it loads from the
local copy and everything runs on this PC, offline too.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from transcriber.fonts import PIP


def detection_status() -> tuple[bool, str]:
    """(ready, message) for showing in Settings."""
    # Only look up the installed package; importing pyannote loads PyTorch, which takes many
    # seconds and would freeze the window. It's loaded for real when a transcription needs it.
    import sys
    from importlib import metadata
    try:
        version = metadata.version("pyannote.audio")
        if not getattr(sys, "frozen", False):
            return True, f"pyannote.audio {version} is installed."
    except metadata.PackageNotFoundError:
        pass
    from transcriber import addons
    if addons.speakers_installed():
        return True, "The speaker detection add-on is installed."
    if getattr(sys, "frozen", False):
        return False, ("Speaker detection is an optional add-on. Install it from Settings > What's installed "
                       "(a one-time download).")
    return False, f"pyannote.audio isn't installed. Run:  {PIP} install pyannote.audio"


def model_downloaded() -> bool:
    """True if the speaker model is already on this computer (then no token is needed)."""
    from transcriber.diarize_worker import model_downloaded as downloaded
    return downloaded()


def usable_token(token: str) -> Optional[str]:
    """What to pass to diarize(): the token, "" when the model is already downloaded and there's
    no token, or None when detection can't run (no token and nothing downloaded yet)."""
    token = (token or "").strip()
    if token:
        return token
    return "" if model_downloaded() else None


def _in_process() -> bool:
    """pyannote is installed in this Python (the Python version of the app)."""
    import importlib.util
    import sys
    return not getattr(sys, "frozen", False) and importlib.util.find_spec("pyannote") is not None


def diarize(wav_path: str, token: str, log: Callable[[str], None] = print) -> tuple[list, dict]:
    """Find who spoke when. Returns (turns, voiceprints):
    turns = [(start, end, "SPEAKER_00"), ...], voiceprints = {"SPEAKER_00": [floats], ...}.
    Runs here when pyannote is installed, otherwise in the speaker detection add-on's own Python."""
    if _in_process():
        from transcriber.diarize_worker import run
        return run(wav_path, token, log)
    from transcriber import addons
    if addons.speakers_installed():
        return addons.run_diarize(wav_path, token, log)
    raise RuntimeError(detection_status()[1])


def assign(segments, turns) -> list[Optional[str]]:
    """Give each transcript line the speaker who talks most during it (or the nearest one)."""
    import bisect
    turns = sorted(turns, key=lambda t: t[0])
    starts = [t[0] for t in turns]
    longest = max((t[1] - t[0] for t in turns), default=0.0)
    labels = []
    for seg in segments:
        start, end = seg.start, max(seg.end, seg.start + 0.01)
        overlap: dict[str, float] = {}
        lo = bisect.bisect_left(starts, start - longest)       # only turns that can overlap this line
        hi = bisect.bisect_left(starts, end)
        for t0, t1, who in turns[lo:hi]:
            o = min(end, t1) - max(start, t0)
            if o > 0:
                overlap[who] = overlap.get(who, 0.0) + o
        if overlap:
            labels.append(max(overlap, key=overlap.get))
        elif turns:
            mid = (start + end) / 2
            labels.append(min(turns, key=lambda t: min(abs(t[0] - mid), abs(t[1] - mid)))[2])
        else:
            labels.append(None)
    return labels


# ---------------------------------------------------------------- the speakers file

def speakers_path(txt_path) -> Path:
    """'Talk (2)_transcript.txt' -> 'Talk (2)_speakers.json' (same folder, same version number)."""
    from transcriber.names import sibling
    return sibling(txt_path, "speakers")


def write_speakers_file(path, *, source, model, language, track, segments, labels, voiceprints, people_ids):
    """Everything needed to show names and rebuild the transcript files later."""
    data = {
        "version": 1, "source": source, "model": model, "language": language, "track": track,
        "segments": [{"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text, "speaker": lab}
                     for s, lab in zip(segments, labels)],
        "speakers": {lab: {"person": people_ids.get(lab), "voiceprint": voiceprints.get(lab)}
                     for lab in sorted({x for x in labels if x})},
    }
    from transcriber.core import write_text_safe
    write_text_safe(path, json.dumps(data))


def read_speakers_file(path) -> Optional[dict]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def display_names(data: dict, people) -> dict[str, str]:
    """SPEAKER_00 -> 'Mel' (a known person) or 'Speaker 1' (numbered by who talks first)."""
    order = []
    for seg in data.get("segments", []):
        lab = seg.get("speaker")
        if lab and lab not in order:
            order.append(lab)
    names, n = {}, 0
    for lab in order:
        person = people.get((data["speakers"].get(lab) or {}).get("person"))
        if person:
            names[lab] = person["name"]
        else:
            n += 1
            names[lab] = f"Speaker {n}"
    return names


def has_speakers(files: dict) -> bool:
    """True when a version's lines say who's talking. (Every version keeps its lines in the
    speakers file, so having the file alone doesn't mean speakers were detected.)"""
    sp = files.get("speakers")
    data = read_speakers_file(sp) if sp else None
    return bool(data) and any(s.get("speaker") for s in data.get("segments", []))


def rows_for(files: dict, people) -> list[tuple]:
    """A saved version's lines as (start seconds, speaker label, name, text, person id) for showing.
    Uses the speakers file when there is one, else the plain transcript."""
    sp = files.get("speakers")
    data = read_speakers_file(sp) if sp else None
    if data and data.get("segments"):
        names = display_names(data, people)
        pids = {lab: (info or {}).get("person") for lab, info in data.get("speakers", {}).items()}
        return [(float(s["start"]), s.get("speaker"), names.get(s.get("speaker")), s["text"],
                 pids.get(s.get("speaker")) if people.get(pids.get(s.get("speaker"))) else None)
                for s in data["segments"]]
    from transcriber.files import read_transcript_lines
    src = files.get("txt") or files.get("srt") or files.get("vtt")
    rows = []
    for ts, text in read_transcript_lines(src) if src else []:
        sec = sum(int(x) * m for x, m in zip(ts.split(":"), (3600, 60, 1)))
        rows.append((float(sec), None, None, text, None))
    return rows


def match_people(labels, prints: dict, people) -> tuple[dict, dict]:
    """Recognize known voices. Returns (people_ids, names): each label gets a person if the voice is
    alike enough (each person used once per transcript), else 'Speaker N' in order of first speech."""
    order = []
    for lab in labels:
        if lab and lab not in order:
            order.append(lab)
    scored = []
    for lab in order:
        for p in people.people:
            if p.get("voices") and prints.get(lab):
                from transcriber.people import cosine
                scored.append((max(cosine(prints[lab], v) for v in p["voices"]), lab, p))
    ids, used = {}, set()
    from transcriber.people import MATCH_THRESHOLD
    for score, lab, p in sorted(scored, key=lambda x: -x[0]):
        if score >= MATCH_THRESHOLD and lab not in ids and p["id"] not in used:
            ids[lab] = p["id"]
            used.add(p["id"])
    names, n = {}, 0
    for lab in order:
        if lab in ids:
            names[lab] = people.get(ids[lab])["name"]
        else:
            n += 1
            names[lab] = f"Speaker {n}"
    return ids, names
