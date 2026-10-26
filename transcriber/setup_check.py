"""What's installed: every piece the app uses, whether it's there, what it's for, and how to add
it (a command to copy, or an Install button the app can run itself)."""

from __future__ import annotations

import importlib.util
import sys

from transcriber.fonts import MAC, PIP, WINDOWS

FROZEN = getattr(sys, "frozen", False)


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _vlc_ok() -> bool:
    try:
        import vlc
        return vlc.Instance("--quiet") is not None
    except Exception:
        return False


def _gpu_state() -> tuple[bool, str]:
    """(GPU is used, what's going on)."""
    from transcriber import core
    try:
        if core.engine() == "faster":
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0 and not core._gpu_broken:
                return True, "faster-whisper is using your NVIDIA GPU."
        if _has("torch"):
            import torch
            if torch.cuda.is_available():
                return True, "PyTorch can use your NVIDIA GPU."
    except Exception:
        pass
    return False, "Transcribing on the CPU."


def components(vlc_ok=None) -> list:
    """[{"key", "name", "purpose", "ok", "status", "fix", "install"}] in order of importance.
    install = ("pip", [packages], [extra pip args]) | ("addon", None, None) | None (do it yourself)."""
    from transcriber.core import check_ffmpeg
    from transcriber.speakers import detection_status
    out = []

    ok = check_ffmpeg()
    out.append(dict(key="ffmpeg", name="ffmpeg", purpose="Reads the sound from your videos. Required.", ok=ok,
                    status="Installed" if ok else "Missing",
                    fix=("brew install ffmpeg" if MAC else "winget install Gyan.FFmpeg" if WINDOWS
                         else "sudo apt install ffmpeg") + "   (then restart the app)",
                    install=None))

    fw, ow = _has("faster_whisper"), _has("whisper")
    out.append(dict(key="engine", name="Speech recognition", purpose="Turns speech into text. Required.",
                    ok=fw or ow, status="faster-whisper" + (" and OpenAI Whisper" if ow else "") if fw else
                    ("OpenAI Whisper (faster-whisper is about 4x faster)" if ow else "Missing"),
                    fix=f"{PIP} install faster-whisper", install=None if FROZEN or fw else ("pip", ["faster-whisper"], [])))

    ok = _vlc_ok() if vlc_ok is None else vlc_ok
    out.append(dict(key="vlc", name="VLC media player", purpose="The video preview and its captions.", ok=ok,
                    status="Installed" if ok else "Missing",
                    fix=("Install VLC from videolan.org" + ("" if FROZEN or _has("vlc") else f", then: {PIP} install python-vlc")
                         + "   (then restart the app)"),
                    install=None if FROZEN or _has("vlc") else ("pip", ["python-vlc"], [])))

    ready, msg = detection_status()
    if FROZEN:
        from transcriber import addons
        out.append(dict(key="speakers", name="Speaker detection", purpose="Labels who's talking on each line.",
                        ok=ready, status="Installed" + (" (GPU)" if addons.addon_info().get("gpu") else "") if ready
                        else "Not installed (optional add-on, " + addons.download_size_note() + ")",
                        fix="Click Install to download it once.", install=None if ready else ("addon", None, None)))
    else:
        out.append(dict(key="speakers", name="Speaker detection", purpose="Labels who's talking on each line.",
                        ok=ready, status="Installed" if ready else "Missing", fix=f"{PIP} install pyannote.audio",
                        install=None if ready else ("pip", ["pyannote.audio"], [])))

    ok = _has("soundcard")
    out.append(dict(key="soundcard", name="Voice sample recording", purpose="Recording people in People > mic button.",
                    ok=ok, status="Installed" if ok else "Missing", fix=f"{PIP} install soundcard",
                    install=None if ok or FROZEN else ("pip", ["soundcard"], [])))

    gpu, msg = _gpu_state()
    if WINDOWS:
        from transcriber import addons
        card = addons.nvidia_gpu()
        if gpu:
            fix, inst = "", None
        elif not card:
            fix, inst = "No NVIDIA graphics card found, so the CPU is used. That's fine, just slower.", None
        elif _has("faster_whisper"):
            fix = f"{PIP} install nvidia-cublas-cu12 nvidia-cudnn-cu12   (then restart the app)"
            inst = None if FROZEN else ("pip", ["nvidia-cublas-cu12", "nvidia-cudnn-cu12"], [])
        else:
            fix = "Install PyTorch's GPU version: see pytorch.org"
            inst = None
        out.append(dict(key="gpu", name="NVIDIA GPU", purpose="Much faster transcription.", ok=gpu or not card,
                        status=msg, fix=fix, install=inst))

    ok = _has("tkinterdnd2")
    out.append(dict(key="dnd", name="Drag and drop", purpose="Dropping files onto the app.", ok=ok,
                    status="Installed" if ok else "Missing", fix=f"{PIP} install tkinterdnd2",
                    install=None if ok or FROZEN else ("pip", ["tkinterdnd2"], [])))
    ok = _has("sv_ttk")
    out.append(dict(key="theme", name="Modern look", purpose="The Windows 11 style light and dark theme.", ok=ok,
                    status="Installed" if ok else "Missing", fix=f"{PIP} install sv-ttk",
                    install=None if ok or FROZEN else ("pip", ["sv-ttk"], [])))
    ok = _has("PIL")
    out.append(dict(key="pillow", name="Pictures", purpose="Profile photos and video thumbnails.", ok=ok,
                    status="Installed" if ok else "Missing", fix=f"{PIP} install pillow",
                    install=None if ok or FROZEN else ("pip", ["pillow"], [])))
    return out


def missing_summary() -> list:
    """Names of the important pieces that are missing (for a nudge at startup)."""
    return [c["name"] for c in components() if not c["ok"] and c["key"] in ("ffmpeg", "engine")]
