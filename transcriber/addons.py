"""Optional pieces the app can install for you.

- The speaker detection add-on (for the installed app, which doesn't carry PyTorch): its own small
  Python, downloaded once into the app's settings folder, with PyTorch and pyannote.audio. Speaker
  detection then runs in that Python as a separate program (diarize_worker.py) and sends back the
  result. The GPU build of PyTorch is used when an NVIDIA graphics card is found.
- pip packages, for the Python version of the app (installed into the Python it runs on).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from transcriber.core import _NO_WINDOW
from transcriber.people import APP_DIR

ADDON_DIR = APP_DIR / "addons" / "speakers"
PYTHON_BUILDS = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
TORCH_GPU_INDEX = "https://download.pytorch.org/whl/cu126"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- the speaker detection add-on

def addon_python() -> Path:
    if sys.platform == "win32":
        return ADDON_DIR / "python" / "python.exe"
    return ADDON_DIR / "python" / "bin" / "python3"


def speakers_installed() -> bool:
    return (ADDON_DIR / "installed.json").exists() and addon_python().exists()


def addon_info() -> dict:
    try:
        return json.loads((ADDON_DIR / "installed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def nvidia_gpu() -> bool:
    """An NVIDIA graphics card with its driver (then the GPU build of PyTorch is worth it)."""
    if sys.platform == "darwin":
        return False
    if shutil.which("nvidia-smi"):
        return True
    return Path(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe").exists()


def download_size_note(gpu: Optional[bool] = None) -> str:
    gpu = nvidia_gpu() if gpu is None else gpu
    if sys.platform == "darwin":
        return "about 400 MB to download, about 1 GB on disk"
    return ("about 3 GB to download (GPU version, for your NVIDIA card), about 5 GB on disk" if gpu else
            "about 400 MB to download, about 1.2 GB on disk")


def _worker_source(name: str = "diarize_worker.py") -> Path:
    base = Path(getattr(sys, "_MEIPASS", "")) / "transcriber" if getattr(sys, "frozen", False) else Path(__file__).parent
    return base / name


def _sync_workers() -> None:
    """Keep the add-on's copies of the workers current with the app."""
    for name in ("diarize_worker.py", "nvidia_worker.py"):
        src, dst = _worker_source(name), ADDON_DIR / name
        try:
            if src.exists() and (not dst.exists() or src.read_bytes() != dst.read_bytes()):
                shutil.copy2(src, dst)
        except OSError:
            pass


def nvidia_ready() -> bool:
    """The add-on's Python has a Transformers new enough for the NVIDIA models."""
    return speakers_installed() and any((ADDON_DIR).glob("**/site-packages/transformers/models/nemotron3_diarization"))


def install_nvidia(log: Callable[[str], None], cancel: Callable[[], bool] = lambda: False) -> None:
    """Add Transformers (for NVIDIA Parakeet and Nemotron) to the speaker detection add-on."""
    if not speakers_installed():
        raise RuntimeError("Install speaker detection first: the NVIDIA models use its Python.")
    _run_logged([str(addon_python()), "-m", "pip", "install", "--no-input", "--progress-bar", "off", "--upgrade",
                 "transformers>=5.21"], log, cancel)
    _sync_workers()


def run_nvidia(kind: str, wav_path: str, token: str, log: Callable[[str], None], on_line=None,
               should_cancel: Callable[[], bool] = lambda: False):
    """NVIDIA Parakeet ("transcribe": lines) or Nemotron ("diarize": (turns, voiceprints)) in the
    add-on's Python. Same results as nvidia_worker.transcribe() / diarize()."""
    if not nvidia_ready():
        raise RuntimeError("The NVIDIA models aren't installed. Settings > What's installed > NVIDIA models.")
    _sync_workers()
    env = dict(os.environ, HF_TOKEN=token or "", PYTHONUNBUFFERED="1")
    p = subprocess.Popen([str(addon_python()), str(ADDON_DIR / "nvidia_worker.py"), kind, wav_path],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=_NO_WINDOW, env=env)
    result, error = None, None
    for line in p.stdout:
        if should_cancel():
            p.kill()
            from transcriber.core import TranscriptionCancelled
            raise TranscriptionCancelled()
        line = line.rstrip()
        if line.startswith("LOG: "):
            log(line[5:])
        elif line.startswith("SEGMENT: ") and on_line:
            on_line(json.loads(line[9:]))
        elif line.startswith("RESULT: "):
            result = json.loads(line[8:])
        elif line.startswith("ERROR: "):
            error = line[7:]
    err_text = p.stderr.read()
    p.wait()
    if result is None:
        tail = [x for x in err_text.strip().splitlines() if x.strip()][-1:]
        raise RuntimeError(error or (tail[0] if tail else "The NVIDIA model stopped unexpectedly."))
    if kind == "transcribe":
        return result["lines"]
    return [tuple(t) for t in result["turns"]], result["prints"]


def _ssl_context():
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _get_json(url: str) -> dict:
    import urllib.request
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "WhisperTranscriber"})
    with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as r:
        return json.loads(r.read().decode("utf-8"))


def _python_asset() -> tuple[str, str]:
    """(name, download url) of a ready-made Python 3.12 for this computer."""
    import platform
    machine = platform.machine().lower()
    if sys.platform == "win32":
        triple = "x86_64-pc-windows-msvc"
    elif sys.platform == "darwin":
        triple = "aarch64-apple-darwin" if machine in ("arm64", "aarch64") else "x86_64-apple-darwin"
    else:
        triple = ("aarch64" if machine in ("arm64", "aarch64") else "x86_64") + "-unknown-linux-gnu"
    rel = _get_json(PYTHON_BUILDS)
    found = [(a["name"], a["browser_download_url"]) for a in rel.get("assets", [])
             if a["name"].startswith("cpython-3.12.") and triple in a["name"]
             and a["name"].endswith(("install_only.tar.gz", "install_only_stripped.tar.gz"))]
    found.sort(key=lambda x: ("stripped" in x[0], "debug" in x[0]))        # the plain build first
    if found:
        return found[0]
    raise RuntimeError("Couldn't find a Python download for this computer. Try again later.")


def _download(url: str, dest: Path, progress: Callable[[float, str], None], cancel: Callable[[], bool]):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "WhisperTranscriber"})
    with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancel():
                raise Cancelled()
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                progress(done / total, f"Downloading Python... {done / 1e6:.0f} of {total / 1e6:.0f} MB")


def _run_logged(cmd: list, log: Callable[[str], None], cancel: Callable[[], bool]) -> None:
    """Run a command, passing its output lines to log. Raises if it fails or is cancelled."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                         creationflags=_NO_WINDOW, env=dict(os.environ, PYTHONUNBUFFERED="1",
                                                            PIP_DISABLE_PIP_VERSION_CHECK="1"))
    last = []
    for line in p.stdout:
        line = line.rstrip()
        if line:
            last = (last + [line])[-6:]
            log(line)
        if cancel():
            p.kill()
            raise Cancelled()
    if p.wait() != 0:
        raise RuntimeError("\n".join(last) or f"exit code {p.returncode}")


def install_speakers(log: Callable[[str], None], progress: Callable[[float, str], None],
                     cancel: Callable[[], bool] = lambda: False, gpu: Optional[bool] = None) -> None:
    """Download a small Python and install PyTorch + pyannote.audio into it (one time)."""
    gpu = nvidia_gpu() if gpu is None else gpu
    tmp = Path(tempfile.mkdtemp(prefix="wt_addon_"))
    new_dir = ADDON_DIR.with_name("speakers-installing")
    try:
        progress(0.0, "Finding a Python download...")
        name, url = _python_asset()
        log(f"Python: {name}")
        archive = tmp / name
        _download(url, archive, lambda f, t: progress(0.1 * f, t), cancel)
        progress(0.1, "Unpacking Python...")
        shutil.rmtree(new_dir, ignore_errors=True)
        new_dir.mkdir(parents=True)
        with tarfile.open(archive) as tf:
            try:
                tf.extractall(new_dir, filter="data")
            except TypeError:                      # Python before 3.12
                tf.extractall(new_dir)
        py = new_dir / addon_python().relative_to(ADDON_DIR)
        pip = [str(py), "-m", "pip", "install", "--no-input", "--progress-bar", "off"]
        progress(0.15, "Installing PyTorch" + (" (GPU version, the biggest part)..." if gpu else "..."))
        if sys.platform == "darwin":
            _run_logged(pip + ["torch", "torchaudio"], log, cancel)
        else:
            _run_logged(pip + ["torch", "torchaudio", "--index-url", TORCH_GPU_INDEX if gpu else TORCH_CPU_INDEX],
                        log, cancel)
        progress(0.75, "Installing pyannote.audio...")
        _run_logged(pip + ["pyannote.audio"], log, cancel)
        progress(0.95, "Finishing...")
        shutil.copy2(_worker_source(), new_dir / "diarize_worker.py")
        shutil.copy2(_worker_source("nvidia_worker.py"), new_dir / "nvidia_worker.py")
        (new_dir / "installed.json").write_text(json.dumps(
            {"installed": time.strftime("%Y-%m-%d %H:%M"), "gpu": gpu, "python": name}), encoding="utf-8")
        shutil.rmtree(ADDON_DIR, ignore_errors=True)
        new_dir.rename(ADDON_DIR)
        progress(1.0, "Speaker detection is installed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(new_dir, ignore_errors=True)


def remove_speakers() -> None:
    shutil.rmtree(ADDON_DIR, ignore_errors=True)


def run_diarize(wav_path: str, token: str, log: Callable[[str], None]) -> tuple[list, dict]:
    """Speaker detection in the add-on's Python. Same result as diarize_worker.run()."""
    worker = ADDON_DIR / "diarize_worker.py"
    _sync_workers()                        # keep the add-on's copies current with the app
    env = dict(os.environ, HF_TOKEN=token or "", PYTHONUNBUFFERED="1")
    p = subprocess.Popen([str(addon_python()), str(worker), wav_path], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, creationflags=_NO_WINDOW, env=env)
    result, error = None, None
    for line in p.stdout:
        line = line.rstrip()
        if line.startswith("LOG: "):
            log(line[5:])
        elif line.startswith("RESULT: "):
            result = json.loads(line[8:])
        elif line.startswith("ERROR: "):
            error = line[7:]
    err_text = p.stderr.read()
    p.wait()
    if result is None:
        tail = [x for x in err_text.strip().splitlines() if x.strip()][-1:]
        raise RuntimeError(error or (tail[0] if tail else "The speaker detection add-on stopped unexpectedly."))
    return [tuple(t) for t in result["turns"]], result["prints"]


# ---------------------------------------------------------------- pip, for the Python version

def torch_build() -> str:
    """The installed PyTorch's build, without importing it: "cpu", "cu126"..., or "" if none."""
    from importlib import metadata
    try:
        version = metadata.version("torch")
    except metadata.PackageNotFoundError:
        return ""
    return version.split("+", 1)[1] if "+" in version else ("cpu" if sys.platform == "win32" else "default")


def nvidia_install_plan() -> dict:
    """What the NVIDIA models still need in this Python: {"torch_gpu": bool, "transformers": bool}."""
    import importlib.util
    try:
        new_enough = importlib.util.find_spec("transformers.models.nemotron3_diarization") is not None
    except (ImportError, ValueError):
        new_enough = False
    build = torch_build()
    return {"torch_gpu": nvidia_gpu() and build in ("", "cpu"), "transformers": not new_enough}


def install_after_close(plan: dict) -> None:
    """Windows, Python version: PyTorch is in use while the app runs (its files are locked), so a
    small window installs after the app closes, then opens the app again. Call, then quit the app."""
    py, pyw = app_python(), sys.executable
    script = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    relaunch = f'start "" "{pyw}" "{script}"' if script else f'start "" "{pyw}" -m transcriber'
    steps = []
    if plan.get("torch_gpu"):
        steps.append(f'"{py}" -m pip install --upgrade --force-reinstall --no-deps --progress-bar on torch '
                     f'--index-url {TORCH_GPU_INDEX}')
    steps.append(f'"{py}" -m pip install --upgrade --progress-bar on "transformers>=5.21"')
    lines = ["@echo off", "chcp 65001 >nul", "title Whisper Transcriber: installing the NVIDIA models",
             "echo Installing the NVIDIA models' parts" + (" (the GPU version of PyTorch is about 3 GB)" if
                                                          plan.get("torch_gpu") else "") + ".",
             "echo The app opens again by itself when it's done.", "echo.",
             ":wait", f'tasklist /FI "PID eq {os.getpid()}" 2>nul | find "{os.getpid()}" >nul',
             "if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)"]
    for st in steps:
        lines += [st, "if errorlevel 1 goto failed"]
    lines += ["echo.", "echo Done.", relaunch, "exit /b 0", ":failed", "echo.",
              "echo Something went wrong (see above). The app opens again; try again from What's installed.",
              "pause", relaunch]
    bat = Path(tempfile.gettempdir()) / "whisper_transcriber_nvidia_install.bat"
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8", newline="")
    os.startfile(str(bat))      # its own console window, so the progress shows

def app_python() -> str:
    """The Python the app runs on (python.exe, not the windowless pythonw.exe)."""
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        exe = exe[:-len("pythonw.exe")] + "python.exe"
    return exe


def pip_install(packages: list, log: Callable[[str], None], cancel: Callable[[], bool] = lambda: False,
                extra: Optional[list] = None) -> None:
    _run_logged([app_python(), "-m", "pip", "install", "--no-input", "--progress-bar", "off", *packages,
                 *(extra or [])], log, cancel)
