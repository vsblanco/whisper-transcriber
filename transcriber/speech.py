"""Extract speech before transcribing: Demucs pulls the voice out of a track, leaving game sound,
music and room noise behind, so Whisper hears a cleaner voice. (It separates voice from
everything else, not one person from another: a mic that also picks up the person next to
you still carries their voice.)

Optional: `pip install demucs` (PyTorch, and a ~80 MB model it downloads the first time). Runs
as its own process, on the GPU when PyTorch has one."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

MODEL = "htdemucs"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def python_for() -> str | None:
    """A Python that has demucs: this one, or the add-on Python of an installed app."""
    try:
        if importlib.util.find_spec("demucs") is not None and not getattr(sys, "frozen", False):
            return sys.executable
    except (ImportError, ValueError):
        pass
    try:
        from transcriber.addons import addon_python
        py = addon_python()
        if py.exists():
            r = subprocess.run([str(py), "-c", "import demucs"], capture_output=True, creationflags=_NO_WINDOW, timeout=60)
            if r.returncode == 0:
                return str(py)
    except Exception:
        pass
    return None


def available() -> bool:
    return python_for() is not None


def extract(wav_path: str, log: Callable[[str], None] = print, cancel: Callable[[], bool] = lambda: False,
            python: str | None = None) -> bool:
    """Replace the 16 kHz mono WAV at wav_path with just the voice in it. False (file untouched)
    when demucs isn't there or fails; the reason goes to log."""
    python = python or python_for()
    if not python:
        log("Extract speech: demucs isn't installed (pip install demucs), transcribing the sound as it is.")
        return False
    tmp = tempfile.mkdtemp(prefix="wt_speech_")
    try:
        log("Extracting the voice from the sound (Demucs)...")
        cmd = [python, "-m", "demucs.separate", "--two-stems=vocals", "-n", MODEL, "-o", tmp, wav_path]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                creationflags=_NO_WINDOW, env=env)
        last = ""
        assert proc.stdout is not None
        for line in proc.stdout:
            last = line.strip() or last
            if cancel():
                proc.kill()
                return False
        proc.wait()
        vocals = next(Path(tmp).rglob("vocals.wav"), None)
        if proc.returncode != 0 or vocals is None:
            log(f"Extract speech: Demucs didn't finish ({last[:160] or 'no output'}); transcribing the sound as it is.")
            return False
        out = wav_path + ".voice.wav"
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(vocals), "-ac", "1", "-ar", "16000", out],
                           capture_output=True, text=True, creationflags=_NO_WINDOW)
        if r.returncode != 0 or not os.path.exists(out):
            log("Extract speech: couldn't convert the voice track; transcribing the sound as it is.")
            return False
        os.replace(out, wav_path)
        log("Voice extracted.")
        return True
    except Exception as exc:
        log(f"Extract speech: {exc}; transcribing the sound as it is.")
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
