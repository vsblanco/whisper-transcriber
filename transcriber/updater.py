"""Updates: check GitHub for a newer release, then update the way this copy was installed.

- Windows installer: download the new Setup .exe and run it quietly (the app closes first and the
  installer reopens it).
- Mac app: download the new .zip, swap the app in place, reopen it.
- Python version (a git clone, e.g. from GitHub Desktop): "git pull" when git is available,
  otherwise point to the download page.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from transcriber import __version__
from transcriber.core import _NO_WINDOW

REPO = "vsblanco/whisper-transcriber"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
ROOT = Path(__file__).resolve().parents[1]          # the repo folder (Python version)


class Cancelled(Exception):
    pass


def parse_version(text: str) -> tuple:
    """'v2.10.1' -> (2, 10, 1). Unknown parts count as 0."""
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


def install_kind() -> str:
    """How this copy was installed: 'windows', 'mac' (the installed apps), 'git' or 'source'."""
    if getattr(sys, "frozen", False):
        return "windows" if sys.platform == "win32" else "mac" if sys.platform == "darwin" else "source"
    return "git" if (ROOT / ".git").exists() else "source"


def _asset_for(release: dict) -> Optional[dict]:
    """The download for this computer, if the release has one."""
    import platform
    for a in release.get("assets", []):
        n = a["name"].lower()
        if sys.platform == "win32" and n.startswith("whisper-transcriber-setup") and n.endswith(".exe"):
            return a
        if sys.platform == "darwin" and n.endswith(".zip") and "mac" in n:
            arm = platform.machine().lower() in ("arm64", "aarch64")
            if ("apple-silicon" in n) == arm:
                return a
    return None


def check() -> Optional[dict]:
    """The newest release if it's newer than this copy: {"version", "notes", "page", "asset"}.
    Raises on network trouble (callers decide whether to say so)."""
    from transcriber.addons import _get_json
    rel = _get_json(LATEST)
    tag = rel.get("tag_name") or rel.get("name") or ""
    if not tag or parse_version(tag) <= parse_version(__version__):
        return None
    return {"version": tag.lstrip("vV"), "notes": (rel.get("body") or "").strip(),
            "page": rel.get("html_url") or RELEASES_PAGE, "asset": _asset_for(rel)}


def download(asset: dict, progress: Callable[[float, str], None], cancel: Callable[[], bool]) -> Path:
    import urllib.request
    from transcriber.addons import _ssl_context
    dest = Path(tempfile.mkdtemp(prefix="wt_update_")) / asset["name"]
    req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "WhisperTranscriber"})
    with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or asset.get("size") or 0)
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
                progress(done / total, f"Downloading... {done / 1e6:.0f} of {total / 1e6:.0f} MB")
    return dest


def apply(path: Path) -> None:
    """Start installing the downloaded update. The app must quit right after this returns."""
    if sys.platform == "win32":
        # The installer closes nothing itself (the app quits first), installs over the old copy, and
        # reopens the app when it's done.
        subprocess.Popen([str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | _NO_WINDOW, close_fds=True)
        return
    if sys.platform == "darwin":
        app = Path(sys.executable).resolve().parents[2]          # .../Whisper Transcriber.app
        if app.suffix != ".app":
            raise RuntimeError("Couldn't find the app to replace.")
        unpacked = path.parent / "new"
        subprocess.run(["ditto", "-x", "-k", str(path), str(unpacked)], check=True)
        new_app = next(unpacked.glob("*.app"), None)
        if new_app is None:
            raise RuntimeError("The download didn't contain the app.")
        script = path.parent / "update.sh"
        script.write_text(f"""#!/bin/bash
while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.5; done
rm -rf "{app}.old"
mv "{app}" "{app}.old" && mv "{new_app}" "{app}" && rm -rf "{app}.old"
xattr -dr com.apple.quarantine "{app}" 2>/dev/null
open "{app}"
""", encoding="utf-8")
        script.chmod(0o755)
        subprocess.Popen(["/bin/bash", str(script)], start_new_session=True, close_fds=True)
        return
    raise RuntimeError("Updating isn't supported here; download the new version from GitHub.")


def find_git() -> Optional[str]:
    """git on the PATH, or the copy that comes with GitHub Desktop (Windows)."""
    git = shutil.which("git")
    if git or sys.platform != "win32":
        return git
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "GitHubDesktop"
    found = sorted(base.glob("app-*/resources/app/git/cmd/git.exe"))
    return str(found[-1]) if found else None


def git_pull(log: Callable[[str], None]) -> str:
    """Update the Python version with git. Returns what happened."""
    git = find_git()
    if not git:
        raise RuntimeError("git isn't on this computer's PATH. Use GitHub Desktop: Fetch origin, then Pull.")
    r = subprocess.run([git, "-C", str(ROOT), "pull", "--ff-only"], capture_output=True, text=True,
                       creationflags=_NO_WINDOW)
    for line in (r.stdout + r.stderr).splitlines():
        if line.strip():
            log(line)
    if r.returncode != 0:
        tail = [x for x in (r.stderr or r.stdout).splitlines() if x.strip()][-1:]
        raise RuntimeError(tail[0] if tail else "git pull didn't work")
    return "Updated. Restart the app to use the new version."


def due(last_check: float, every_hours: float = 20) -> bool:
    return time.time() - (last_check or 0) > every_hours * 3600
