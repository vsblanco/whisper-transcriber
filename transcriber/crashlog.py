"""Crash and error logs, so a problem can be looked at afterwards.

Kept in the app's settings folder, under logs/ (Windows: %APPDATA%\\WhisperTranscriber\\logs):

- errors.log      every error the app caught or didn't (newest at the end, trimmed when it gets big),
                  with what was on screen at the time
- last_run.log    this run's own output, plus Python's crash report if the app dies outright
                  (a crash inside VLC or another library). Started fresh each run.
- crash-<date>.log  a copy of last_run.log, made when a run didn't end normally

Nothing leaves the computer.
"""

from __future__ import annotations

import faulthandler
import os
import platform
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

from transcriber.people import APP_DIR

LOG_DIR = APP_DIR / "logs"
ERRORS = LOG_DIR / "errors.log"
LAST_RUN = LOG_DIR / "last_run.log"
CLEAN_EXIT = "=== closed normally ==="
MAX_ERRORS_BYTES = 2_000_000
MAX_RUN_BYTES = 5_000_000
KEEP_CRASHES = 20

_context: Optional[Callable[[], str]] = None
_run_file = None
_lock = threading.Lock()
previous_crash: Optional[Path] = None      # set at start when the last run didn't end normally


class _Capped:
    """A stream that stops growing past a size (progress bars can print a lot)."""

    def __init__(self, f, limit: int):
        self.f, self.limit, self.n = f, limit, 0

    def write(self, s):
        if self.n < self.limit:
            self.n += len(s)
            try:
                self.f.write(s)
                self.f.flush()
            except (OSError, ValueError):
                pass
        return len(s)

    def flush(self):
        try:
            self.f.flush()
        except (OSError, ValueError):
            pass

    def fileno(self):
        return self.f.fileno()

    def isatty(self):
        return False


def _header() -> str:
    from transcriber import __version__
    return (f"Whisper Transcriber {__version__} | {platform.platform()} | Python {platform.python_version()}"
            f" | {'installed app' if getattr(sys, 'frozen', False) else 'Python version'}")


def start(context: Optional[Callable[[], str]] = None) -> None:
    """Call once at startup: keep the last run's report if it crashed, start this run's log, and
    catch errors from now on."""
    global _context, _run_file, previous_crash
    _context = context
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    # Did the last run end normally? If not, keep its log as a crash report.
    try:
        if LAST_RUN.exists():
            text = LAST_RUN.read_text(encoding="utf-8", errors="replace")
            if text.strip() and CLEAN_EXIT not in text:
                previous_crash = LOG_DIR / time.strftime("crash-%Y%m%d-%H%M%S.log",
                                                         time.localtime(LAST_RUN.stat().st_mtime))
                LAST_RUN.replace(previous_crash)
        crashes = sorted(LOG_DIR.glob("crash-*.log"))
        for old in crashes[:-KEEP_CRASHES]:
            old.unlink()
    except OSError:
        pass
    try:
        _run_file = open(LAST_RUN, "w", encoding="utf-8", buffering=1)
        _run_file.write(f"=== started {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n{_header()}\n")
        faulthandler.enable(file=_run_file, all_threads=True)      # outright crashes land here
    except OSError:
        _run_file = None
    # Without a console (the installed app, Transcriber.pyw) output went nowhere: keep it here.
    if _run_file is not None:
        if sys.stderr is None or getattr(sys.stderr, "name", "") == os.devnull:
            sys.stderr = _Capped(_run_file, MAX_RUN_BYTES)
    sys.excepthook = _excepthook
    threading.excepthook = _thread_hook


def stop() -> None:
    """Call when the app closes normally."""
    if _run_file is not None:
        try:
            _run_file.write(f"{CLEAN_EXIT} {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            _run_file.flush()
        except (OSError, ValueError):
            pass


def log_error(where: str, exc: Optional[BaseException] = None, tb=None) -> None:
    """Add an error to errors.log with what was going on."""
    try:
        if exc is not None:
            detail = "".join(traceback.format_exception(type(exc), exc, tb or exc.__traceback__))
        else:
            detail = traceback.format_exc()
        ctx = ""
        if _context:
            try:
                ctx = _context()
            except Exception:
                ctx = "(couldn't tell)"
        entry = (f"\n----- {time.strftime('%Y-%m-%d %H:%M:%S')}  {where}\n{_header()}\n"
                 + (f"On screen: {ctx}\n" if ctx else "") + detail)
        with _lock:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            if ERRORS.exists() and ERRORS.stat().st_size > MAX_ERRORS_BYTES:
                old = ERRORS.read_text(encoding="utf-8", errors="replace")
                ERRORS.write_text(old[-MAX_ERRORS_BYTES // 2:], encoding="utf-8")
            with open(ERRORS, "a", encoding="utf-8") as f:
                f.write(entry)
        if _run_file is not None:
            _run_file.write(entry)
    except Exception:
        pass


def _excepthook(kind, exc, tb):
    log_error("Unhandled error", exc, tb)
    sys.__excepthook__(kind, exc, tb)


def _thread_hook(args):
    if args.exc_type is SystemExit:
        return
    log_error(f"Unhandled error in background task ({getattr(args.thread, 'name', '?')})", args.exc_value,
              args.exc_traceback)


def open_folder() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    from transcriber.ui_common import show_in_folder
    show_in_folder(ERRORS if ERRORS.exists() else LOG_DIR)
