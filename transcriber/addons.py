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


def _worker_source() -> Path:
    base = Path(getattr(sys, "_MEIPASS", "")) / "transcriber" if getattr(sys, "frozen", False) else Path(__file__).parent
    return base / "diarize_worker.py"


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
    try:                                   # keep the add-on's copy current with the app
        src = _worker_source()
        if src.exists() and src.read_bytes() != worker.read_bytes():
            shutil.copy2(src, worker)
    except OSError:
        pass
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
