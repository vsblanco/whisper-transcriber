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

    out.append(_nvidia_component())

    from transcriber import speech
    ok = speech.available()
    out.append(dict(key="speech", name="Extract speech (Demucs)", purpose="Settings > Transcription > Extract speech "
                    "first: the voice pulled out of game sound, music and noise before transcribing.",
                    ok=ok, status="Installed" if ok else "Missing", fix=f"{PIP} install demucs",
                    install=None if ok or FROZEN else ("pip", ["demucs"], [])))

    ok = _has("soundcard")
    out.append(dict(key="soundcard", name="Voice sample recording", purpose="Recording people in People > mic button.",
                    ok=ok, status="Installed" if ok else "Missing", fix=f"{PIP} install soundcard",
                    install=None if ok or FROZEN else ("pip", ["soundcard"], [])))

    if WINDOWS:
        from transcriber import app_audio
        ok = _has("proctap")
        try:
            old = sys.getwindowsversion().build < app_audio.MIN_BUILD
        except Exception:
            old = False
        out.append(dict(key="appaudio", name="Recording one app", purpose="Record just one app's sound "
                        "(Queue > Record > One app), like a browser or Discord.", ok=ok and not old,
                        status="Installed" if ok and not old else
                        ("Needs Windows 10 version 2004 or newer" if old else "Missing"),
                        fix=f"{PIP} install proc-tap", install=None if ok or old or FROZEN else ("pip", ["proc-tap"], [])))

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


def _nvidia_component() -> dict:
    """NVIDIA Parakeet and Nemotron: Transformers 5.21+, and PyTorch's GPU version when there's a card."""
    from transcriber import addons
    base = dict(key="nvidia", name="NVIDIA models",
                purpose="Parakeet (fast, accurate speech to text) and Nemotron (who's speaking). Optional.")
    if FROZEN:
        ok = addons.nvidia_ready()
        ready = addons.speakers_installed()
        return dict(base, ok=ok, status="Installed" if ok else ("Not installed" if ready else
                                                                 "Install speaker detection first"),
                    fix="Click Install (adds Transformers to the speaker detection add-on)." if ready else
                    "The NVIDIA models use the speaker detection add-on: install that first.",
                    install=None if ok or not ready else ("addon_nvidia", None, None))
    plan = addons.nvidia_install_plan()
    has_torch = _has("torch")
    build = addons.torch_build()
    ok = has_torch and not plan["torch_gpu"] and not plan["transformers"]
    if ok:
        status = "Installed" + (" (on your NVIDIA GPU)" if build not in ("cpu", "default", "") else " (CPU)")
    else:
        bits = []
        if plan["torch_gpu"]:
            bits.append("PyTorch is the CPU version" if has_torch else "PyTorch isn't installed")
        elif not has_torch:
            bits.append("PyTorch isn't installed")
        if plan["transformers"]:
            bits.append("Transformers needs version 5.21 or newer")
        status = "; ".join(bits)
    fix = ("Click Install: " + ("the app closes, a window installs the GPU version of PyTorch (about 3 GB) and "
                               "Transformers, then the app opens again." if plan["torch_gpu"] and WINDOWS else
                               "installs Transformers" + (" and PyTorch." if not has_torch else ".")))
    return dict(base, ok=ok, status=status, fix=fix, install=None if ok else ("nvidia", None, None))


def missing_summary() -> list:
    """Names of the important pieces that are missing (for a nudge at startup)."""
    return [c["name"] for c in components() if not c["ok"] and c["key"] in ("ffmpeg", "engine")]
