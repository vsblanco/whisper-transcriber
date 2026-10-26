"""Transcription: reading audio from media files (ffmpeg), the Whisper engines (faster-whisper or
OpenAI Whisper), and the shared basics (Segment, Transcript, timestamps, safe file writes).

Saved transcript files live in files.py, word timings in words.py, and editing saved transcripts
in edits.py. Shared by the desktop app (app.py) and the command-line tool (transcribe_video.py).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

MODELS = ["tiny", "base", "small", "medium", "large", "turbo"]
MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm", ".wmv", ".flv",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
}

# On Windows, keep ffmpeg/ffprobe from flashing a console window when run from the GUI.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class TranscriptionCancelled(Exception):
    """Raised inside a running transcription when the user cancels."""


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list = field(default_factory=list)   # [(start, end, word), ...] when known


@dataclass
class Transcript:
    source: str
    model: str
    language: str
    segments: list[Segment] = field(default_factory=list)
    text: str = ""
    track: str = ""          # "" = all tracks mixed; otherwise the track's display name
    speakers: list = field(default_factory=list)      # speaker label per segment (from speaker detection)
    voiceprints: dict = field(default_factory=dict)   # speaker label -> voiceprint
    names: dict = field(default_factory=dict)         # speaker label -> name shown ("Mel", "Speaker 1")
    people_ids: dict = field(default_factory=dict)    # speaker label -> People id, when recognized

    def name_at(self, i: int) -> str:
        if i < len(self.speakers) and self.speakers[i]:
            return self.names.get(self.speakers[i], self.speakers[i])
        return ""


# --------------------------------------------------------------------------- helpers

def default_output_dir() -> Path:
    """Documents\\Transcripts for the current user."""
    return Path.home() / "Documents" / "Transcripts"


def format_timestamp(seconds: float, millis: bool = False, sep: str = ",") -> str:
    """HH:MM:SS, or HH:MM:SS,mmm when millis=True (sep '.' for WebVTT)."""
    total_ms = int(round(max(seconds, 0) * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    if millis:
        return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


def check_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, creationflags=_NO_WINDOW)
        subprocess.run(["ffprobe", "-version"], capture_output=True, creationflags=_NO_WINDOW)
        return True
    except FileNotFoundError:
        return False


def media_duration(path: str) -> Optional[float]:
    """Length of the media file in seconds, or None if ffprobe can't tell."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, creationflags=_NO_WINDOW,
        ).stdout.strip()
        return float(out) if out else None
    except (FileNotFoundError, ValueError):
        return None


def write_text_safe(path, text: str) -> None:
    """Write a file so it's either the old version or the new one, never half of each (a dropped
    network connection or a crash mid-write can't leave a cut-off transcript)."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}


def has_video_stream(path: str) -> bool:
    """True if the file has a real video track (cover art in audio files doesn't count)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
             "stream=codec_type:stream_disposition=attached_pic", "-of", "csv=p=0", path],
            capture_output=True, text=True, creationflags=_NO_WINDOW,
        ).stdout
        return any(line.startswith("video") and not line.rstrip().endswith("1") for line in out.splitlines())
    except FileNotFoundError:
        return False


def extract_thumbnail(path: str, out_png: str, width: int, height: int,
                      duration: Optional[float] = None) -> bool:
    """Save one frame from the video as a PNG of exactly width x height (letterboxed if needed).

    Grabs a frame about 10% in, which skips black intro frames. Returns False if it fails.
    """
    at = min(max((duration or 0) * 0.1, 0), 30) if duration else 1
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black")
    for seek in (at, 0):  # very short clips can fail at 10%, so fall back to the first frame
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", f"{seek:.2f}", "-i", path,
                 "-frames:v", "1", "-vf", vf, out_png],
                capture_output=True, timeout=30, creationflags=_NO_WINDOW,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        if os.path.exists(out_png) and os.path.getsize(out_png) > 0:
            return True
    return False


def audio_tracks(path: str) -> list[dict]:
    """The file's audio tracks in order: [{"n": 0, "name": "Mic", "original": "Mic", "channels": 2}, ...].

    "name" is the title saved in the file (OBS writes these if you name tracks), else "Track N".
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
             "stream=index,channels:stream_tags=title,handler_name,language", "-of", "json", path],
            capture_output=True, text=True, creationflags=_NO_WINDOW,
        ).stdout
        streams = json.loads(out or "{}").get("streams", [])
    except (FileNotFoundError, ValueError):
        return []
    tracks = []
    for n, st in enumerate(streams):
        tags = {k.lower(): v for k, v in (st.get("tags") or {}).items()}
        title = (tags.get("title") or "").strip()
        handler = (tags.get("handler_name") or "").strip()
        # Generic handler names ("SoundHandler") say nothing; only use real titles.
        name = title or (handler if handler and "handler" not in handler.lower() else "") or f"Track {n + 1}"
        tracks.append({"n": n, "name": name, "original": name, "channels": st.get("channels") or 0})
    return tracks


_silence_cache: dict[tuple, set] = {}
SILENT_DB = -70.0          # a track whose loudest moment is quieter than this has no real audio


def _file_id(path: str):
    try:
        st = os.stat(path)
        return (os.path.normcase(os.path.abspath(path)), st.st_mtime, st.st_size)
    except OSError:
        return None


def silent_tracks_cached(path: str) -> Optional[set]:
    """The silent tracks found earlier for this file, or None if it hasn't been checked."""
    key = _file_id(path)
    return _silence_cache.get(key) if key else None


def silent_tracks(path: str, count: Optional[int] = None) -> set:
    """Which audio tracks (0-based) are completely silent, e.g. an OBS track with nothing routed
    to it. One pass over the file's audio; the answer is remembered while the app is open."""
    key = _file_id(path)
    if key in _silence_cache:
        return _silence_cache[key]
    if count is None:
        count = len(audio_tracks(path))
    if count < 2:
        _silence_cache[key] = set()
        return set()
    graph = ";".join(f"[0:a:{n}]volumedetect[o{n}]" for n in range(count))
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-vn", "-sn", "-dn", "-i", path, "-filter_complex", graph]
    for n in range(count):
        cmd += ["-map", f"[o{n}]", "-f", "null", "-"]
    try:
        err = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW).stderr or ""
    except (FileNotFoundError, OSError):
        return set()
    loudest: dict[int, float] = {}
    for m in re.finditer(r"Parsed_volumedetect_(\d+)[^\]]*\]\s*max_volume:\s*(-?inf|-?[\d.]+)\s*dB", err):
        loudest[int(m.group(1))] = float("-inf") if "inf" in m.group(2) else float(m.group(2))
    if not loudest:          # couldn't measure: treat everything as having audio
        return set()
    silent = {n for n in range(count) if loudest.get(n, float("-inf")) <= SILENT_DB}
    if len(silent) == count:  # all "silent" is more likely a measuring problem; don't hide everything
        silent = set()
    _silence_cache[key] = silent
    return silent


def safe_label(text: str) -> str:
    """Make a track name safe to use inside a file name."""
    cleaned = re.sub(r'[<>:"/\\|?*\[\]]+', " ", text).strip().rstrip(".")
    return re.sub(r"\s+", " ", cleaned)[:40] or "Track"


def extract_audio(media_path: str, audio_path: str, track: Optional[int] = None,
                  clip: Optional[tuple] = None) -> None:
    """Make a 16 kHz mono WAV for Whisper: every audio track mixed together, or just one
    (track = 0-based position among the file's audio tracks). clip: (start, end) seconds."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
         "-of", "csv=p=0", media_path],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    )
    streams = [s.strip() for s in probe.stdout.splitlines() if s.strip()]
    if not streams:
        raise RuntimeError("No audio track found in this file.")

    cmd = ["ffmpeg", "-y"]
    if clip:            # seek before opening: jumps straight there instead of decoding from the start
        cmd += ["-ss", f"{max(clip[0], 0):.3f}", "-t", f"{max(clip[1] - max(clip[0], 0), 0.1):.3f}"]
    cmd += ["-i", media_path]
    if track is not None:
        if track >= len(streams):
            raise RuntimeError(f"This file has no audio track {track + 1}.")
        cmd += ["-map", f"0:a:{track}"]
    elif len(streams) > 1:
        silent = silent_tracks_cached(media_path) or set()
        mix = [s for n, s in enumerate(streams) if n not in silent] or streams   # skip silent tracks
        if len(mix) == 1:
            cmd += ["-map", f"0:{mix[0]}"]
        else:
            inputs = "".join(f"[0:{s}]" for s in mix)
            cmd += ["-filter_complex", f"{inputs}amix=inputs={len(mix)}:duration=longest"]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", audio_path]

    result = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW)
    if result.returncode != 0 or not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
        tail = (result.stderr or "").strip().splitlines()[-3:]
        raise RuntimeError("ffmpeg could not extract the audio.\n" + "\n".join(tail))


# --------------------------------------------------------------------------- transcription

_model_cache: dict[str, object] = {}
_model_lock = threading.Lock()   # one load at a time (background preload vs. a transcription)

# Whisper's verbose output looks like: [00:12.340 --> 00:15.870]  Some text
_SEGMENT_LINE = re.compile(r"^\[(?:(\d+):)?(\d+):(\d+\.\d+) --> (?:(\d+):)?(\d+):(\d+\.\d+)\]\s*(.*)$")


def _to_seconds(h: Optional[str], m: str, s: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + float(s)


class _SegmentStream(io.TextIOBase):
    """Captures Whisper's verbose stdout and turns each line into a live segment callback.

    Also the cancellation point: raising from write() aborts the running transcription.
    """

    def __init__(self, on_segment: Optional[Callable[[Segment], None]], should_cancel: Callable[[], bool]):
        self._buf = ""
        self._on_segment = on_segment
        self._should_cancel = should_cancel

    def write(self, s: str) -> int:
        if self._should_cancel():
            raise TranscriptionCancelled()
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            m = _SEGMENT_LINE.match(line.strip())
            if m and self._on_segment:
                start = _to_seconds(m.group(1), m.group(2), m.group(3))
                end = _to_seconds(m.group(4), m.group(5), m.group(6))
                self._on_segment(Segment(start, end, m.group(7).strip()))
        return len(s)


# Two engines: faster-whisper (same models, about 4x faster) when it's installed, else OpenAI's.
ENGINES = {"auto": "Automatic", "faster": "faster-whisper", "openai": "OpenAI Whisper"}
_engine_pref = "auto"
_engine_note = ""           # e.g. why faster-whisper fell back to the CPU


def set_engine(pref: str) -> None:
    global _engine_pref
    _engine_pref = pref if pref in ENGINES else "auto"


def faster_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("faster_whisper") is not None


def engine() -> str:
    """The engine in use: "faster" or "openai"."""
    if _engine_pref == "openai" or _use_openai_gpu:
        return "openai"
    if faster_available():
        return "faster"
    return "openai"


def engine_label() -> str:
    return ENGINES[engine()] + (f" ({_engine_note})" if _engine_note and engine() == "faster" else "")


def _faster_name(name: str) -> str:
    """Our model names in faster-whisper's terms."""
    try:
        from faster_whisper.utils import available_models
        names = set(available_models())
    except Exception:
        names = set()
    if name == "large":
        return "large-v3"
    if name == "turbo" and names and "turbo" not in names:
        return "large-v3-turbo" if "large-v3-turbo" in names else "large-v3"
    return name


def model_is_downloaded(name: str) -> bool:
    """True if this model is already downloaded for the engine in use."""
    try:
        if engine() == "faster":
            from faster_whisper.utils import _MODELS
            from huggingface_hub import try_to_load_from_cache
            repo = _MODELS.get(_faster_name(name))
            return bool(repo and isinstance(try_to_load_from_cache(repo, "model.bin"), str))
        import whisper
        url = whisper._MODELS[name]
        root = os.path.join(os.getenv("XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache")), "whisper")
        return os.path.exists(os.path.join(root, os.path.basename(url)))
    except Exception:
        return True  # can't tell; don't claim a download is coming


def is_model_loaded(name: str) -> bool:
    return f"{engine()}:{name}" in _model_cache


def load_model(
    name: str,
    log: Callable[[str], None] = print,
    on_download: Optional[Callable[[int, int], None]] = None,
):
    """Load (and on first use, download) a model. Models are kept in memory for reuse.

    on_download(bytes_done, bytes_total) is called while a first-time download is running
    (OpenAI engine; faster-whisper downloads show as a message).
    """
    with _model_lock:
        if engine() == "faster":
            return _load_faster(name, log)
        return _load_model_locked(name, log, on_download)


_run_lock = threading.Lock()
_gpu_broken = False        # faster-whisper's GPU libraries failed to load this session
_use_openai_gpu = False    # ...and OpenAI Whisper can use the GPU, so it's used instead


def _openai_gpu_works() -> bool:
    import importlib.util
    if importlib.util.find_spec("whisper") is None:
        return False
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def _add_nvidia_dll_dirs() -> None:
    """Let faster-whisper find NVIDIA's GPU libraries installed with pip
    (nvidia-cublas-cu12, nvidia-cudnn-cu12): their DLLs sit in site-packages/nvidia/*/bin."""
    if sys.platform != "win32":
        return
    import site
    roots = list(site.getsitepackages()) + [site.getusersitepackages()]
    for root in roots:
        base = Path(root) / "nvidia"
        if not base.is_dir():
            continue
        for d in base.glob("*/bin"):
            try:
                os.add_dll_directory(str(d))
            except (OSError, AttributeError):
                pass
            if str(d) not in os.environ.get("PATH", ""):
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


def _is_gpu_library_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(k in text for k in ("cublas", "cudnn", "cuda", ".dll", "libcu"))


def _load_faster(name, log):
    global _engine_note
    key = f"faster:{name}"
    if key not in _model_cache:
        _add_nvidia_dll_dirs()
        try:
            import importlib      # a CUDA build of PyTorch brings the GPU libraries faster-whisper uses
            importlib.import_module("torch")
        except Exception:
            pass
        import ctranslate2
        from faster_whisper import WhisperModel
        real = _faster_name(name)
        downloading = not model_is_downloaded(name)
        gpu = ctranslate2.get_cuda_device_count() > 0 and not _gpu_broken
        log(f"Downloading the '{name}' model (first time only)..." if downloading
            else f"Loading '{name}' model on {'GPU' if gpu else 'CPU'} (faster-whisper)...")
        model = None
        if gpu:
            try:
                model = WhisperModel(real, device="cuda", compute_type="float16")
                _engine_note = "GPU"
            except Exception as exc:
                log(f"faster-whisper couldn't use the GPU ({exc}); using the CPU instead.")
        if model is None:
            model = WhisperModel(real, device="cpu", compute_type="int8")
            _engine_note = "CPU"
        _model_cache[key] = model
    return _model_cache[key]


def _load_model_locked(name, log, on_download):
    key = f"openai:{name}"
    if key not in _model_cache:
        import whisper  # imported lazily so the app opens fast
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        downloading = not model_is_downloaded(name)
        log(f"Downloading the Whisper '{name}' model (first time only)..." if downloading
            else f"Loading Whisper '{name}' model on {device.upper()}...")

        original_tqdm = getattr(whisper, "tqdm", None)
        if downloading and on_download and original_tqdm is not None:
            # Whisper reports download progress through tqdm; route it to our callback instead.
            class _Progress:
                def __init__(self, total=0, **_):
                    self.total, self.n = int(total or 0), 0

                def update(self, k):
                    self.n += k
                    on_download(self.n, self.total)

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

            whisper.tqdm = _Progress
        try:
            _model_cache[key] = whisper.load_model(name, device=device)
        finally:
            if original_tqdm is not None:
                whisper.tqdm = original_tqdm
        if downloading:
            log(f"Loaded Whisper '{name}' model on {device.upper()}.")
    return _model_cache[key]


def whisper_device() -> str:
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "unknown"


def vocab_prompt(words: list) -> Optional[str]:
    """Whisper's starting prompt from a list of names and terms, so they're spelled right. Kept
    short: a long prompt makes Whisper more likely to repeat it on silence."""
    seen, out = set(), []
    for w in words:
        w = " ".join(str(w).split()).strip(" ,;")
        if w and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    text = ""
    for w in out:
        if len(text) + len(w) > 400:
            break
        text += (", " if text else "") + w
    return f"Glossary: {text}." if text else None


def split_words(text: str) -> list:
    """A word list typed in Settings or People: commas or new lines between entries."""
    return [w.strip() for w in re.split(r"[,\n;]+", text or "") if w.strip()]


def _run_openai(model, audio_path, language, on_segment, should_cancel, prompt=None):
    stream = _SegmentStream(on_segment, should_cancel)
    with contextlib.redirect_stdout(stream):
        result = model.transcribe(
            audio_path,
            language=language or None,
            verbose=True,                       # prints each segment; captured above
            fp16=whisper_device() == "cuda",    # fp16 only works on GPU
            word_timestamps=True,
            initial_prompt=prompt,
        )
    segments = [Segment(s["start"], s["end"], s["text"].strip(),
                        [(w["start"], w["end"], w["word"]) for w in s.get("words") or []])
                for s in result["segments"]]
    return segments, result.get("language", language or ""), result["text"].strip()


def _read_wav_array(path: str):
    """The 16 kHz mono WAV made by extract_audio, as the float32 array faster-whisper takes.
    (Passing the array skips faster-whisper's own audio reader, PyAV, whose newer versions
    break with "open() got an unexpected keyword argument 'metadata_errors'".)"""
    import wave
    import numpy as np
    with wave.open(path, "rb") as w:
        frames, width = w.readframes(w.getnframes()), w.getsampwidth()
    data = np.frombuffer(frames, dtype=np.int16 if width == 2 else np.int32).astype(np.float32)
    return data / (32768.0 if width == 2 else 2147483648.0)


def _run_faster(model, audio_path, language, on_segment, should_cancel, prompt=None):
    gen, info = model.transcribe(_read_wav_array(audio_path), language=language or None, word_timestamps=True,
                                 initial_prompt=prompt)
    segments = []
    for s in gen:                                # recognized one line at a time
        if should_cancel():
            raise TranscriptionCancelled()
        seg = Segment(s.start, s.end, s.text.strip(), [(w.start, w.end, w.word) for w in (s.words or [])])
        segments.append(seg)
        if on_segment:
            on_segment(Segment(seg.start, seg.end, seg.text))
    return segments, info.language or language or "", " ".join(x.text for x in segments)


def transcribe_file(
    media_path: str,
    model_name: str = "medium",
    language: Optional[str] = None,
    on_segment: Optional[Callable[[Segment], None]] = None,
    should_cancel: Callable[[], bool] = lambda: False,
    log: Callable[[str], None] = print,
    track: Optional[int] = None,
    track_name: str = "",
    speaker_token: Optional[str] = None,
    prompt: Optional[str] = None,
    clip: Optional[tuple] = None,
) -> Transcript:
    """Transcribe one audio or video file (all tracks mixed, or one track). Calls on_segment as
    each line is recognized. prompt: names and terms to expect (vocab_prompt). clip: (start, end)
    seconds to transcribe just that part (times in the result are still the file's)."""
    if not os.path.exists(media_path):
        raise FileNotFoundError(media_path)

    fd, audio_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        log("Extracting audio...")
        extract_audio(media_path, audio_path, track, clip)
        if should_cancel():
            raise TranscriptionCancelled()

        if not _run_lock.acquire(blocking=False):      # one transcription at a time (they share the model,
            log("Waiting for the other transcription to finish...")   # the GPU and, for OpenAI Whisper, stdout)
            _run_lock.acquire()
        try:
            return _transcribe_locked(media_path, audio_path, model_name, language, on_segment, should_cancel,
                                      log, track, track_name, speaker_token, prompt, clip)
        finally:
            _run_lock.release()
    finally:
        with contextlib.suppress(OSError):
            os.remove(audio_path)


def _transcribe_locked(media_path, audio_path, model_name, language, on_segment, should_cancel, log, track,
                       track_name, speaker_token, prompt, clip) -> Transcript:
    global _gpu_broken, _use_openai_gpu
    model = load_model(model_name, log)
    log("Transcribing...")
    run = _run_faster if engine() == "faster" else _run_openai
    try:
        segments, lang, text = run(model, audio_path, language, on_segment, should_cancel, prompt)
    except Exception as exc:
        # faster-whisper loads the GPU libraries only when it starts transcribing. If they're
        # missing, switch to the CPU for the rest of this session and try again.
        if engine() != "faster" or _gpu_broken or not _is_gpu_library_error(exc):
            raise
        _gpu_broken = True
        _model_cache.pop(f"faster:{model_name}", None)
        hint = "To use faster-whisper on the GPU: py -3.12 -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12" if sys.platform == "win32" else ""
        if _openai_gpu_works():          # OpenAI Whisper on the GPU beats faster-whisper on the CPU
            _use_openai_gpu = True
            log(f"faster-whisper couldn't use the GPU ({exc}). Using OpenAI Whisper on the GPU instead. {hint}")
            run = _run_openai
        else:
            log(f"The GPU couldn't be used ({exc}). Transcribing on the CPU instead. {hint}")
        model = load_model(model_name, log)
        segments, lang, text = run(model, audio_path, language, on_segment, should_cancel, prompt)
    if clip:                                # back onto the file's own times
        for sg in segments:
            sg.start, sg.end = sg.start + clip[0], sg.end + clip[0]
            sg.words = [(a + clip[0], b + clip[0], w) for a, b, w in sg.words]
    labels, prints = [], {}
    if speaker_token is not None and segments and not should_cancel():
        try:
            from transcriber.speakers import assign, diarize
            turns, prints = diarize(audio_path, speaker_token, log)
            labels = assign(segments, turns)
        except Exception as exc:   # a failed speaker step shouldn't lose the transcript
            log(f"Speaker detection didn't work: {exc}")
            labels, prints = [], {}

    return Transcript(
        source=media_path,
        model=model_name,
        language=lang,
        segments=segments,
        text=text,
        track=track_name if track is not None else "",
        speakers=labels,
        voiceprints=prints,
    )
