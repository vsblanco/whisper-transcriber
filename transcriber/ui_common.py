"""Settings, colors and small helpers shared by the app's windows."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path


APP_NAME = "Whisper Transcriber"
if sys.platform == "darwin":
    SETTINGS_PATH = Path.home() / "Library" / "Application Support" / "WhisperTranscriber" / "settings.json"
else:
    SETTINGS_PATH = Path(os.environ.get("APPDATA", Path.home() / ".config")) / "WhisperTranscriber" / "settings.json"

LANGUAGES = [
    ("Auto-detect", ""), ("English", "en"), ("Spanish", "es"), ("Portuguese", "pt"),
    ("French", "fr"), ("Italian", "it"), ("German", "de"), ("Japanese", "ja"),
    ("Korean", "ko"), ("Chinese", "zh"), ("Russian", "ru"), ("Arabic", "ar"),
]
MODEL_NOTES = {
    "tiny": "fastest, least accurate",
    "base": "fast, rough",
    "small": "balanced",
    "medium": "accurate (recommended)",
    "large": "most accurate, slowest",
    "turbo": "near-large accuracy, much faster",
}

PENDING, WORKING, DONE, FAILED, CANCELLED = "Waiting", "Transcribing", "Done", "Failed", "Cancelled"

APPEARANCES = ["System", "Light", "Dark"]

# Colors for the parts ttk themes don't cover (the transcript box and its text styles).
PALETTE = {
    "light": {"bg": "#ffffff", "fg": "#1c1c1c", "muted": "#6b6b6b", "ts": "#8a8a8a",
              "info": "#767676", "err": "#b3261e", "select": "#cfe3ff",
              "band": "#f3f4f6", "hover": "#ebedf0", "now": "#dce8f8"},
    "dark":  {"bg": "#202020", "fg": "#e8e8e8", "muted": "#9d9d9d", "ts": "#7a7a7a",
              "info": "#a0a0a0", "err": "#ff8a80", "select": "#2d4a73",
              "band": "#282828", "hover": "#2f2f2f", "now": "#2f3a4a"},
}


DROP_ACCENT = {"light": "#2f6fbf", "dark": "#4cc2ff"}


def make_vignette(w: int, h: int, theme: str, step: int = 3) -> tk.PhotoImage:
    """A dark vignette: the list background slightly dimmed in the middle, darkening toward the
    edges. Drawn at 1/step resolution and scaled up, which keeps it fast and still smooth."""
    bg = ICON_COLORS[theme]["bg"]   # matches the list background
    b = tuple(int(bg[i:i + 2], 16) for i in (1, 3, 5))
    dark = (8, 10, 14)
    center, edge = (0.12, 0.62) if theme == "light" else (0.25, 0.85)
    sw, sh = max(w // step, 2), max(h // step, 2)
    rows = []
    for y in range(sh):
        ny = (y + 0.5) / sh * 2 - 1
        row = []
        for x in range(sw):
            nx = (x + 0.5) / sw * 2 - 1
            d = (abs(nx) ** 3 + abs(ny) ** 3) ** (1 / 3)   # rounded-rectangle distance from center
            t = center + (edge - center) * min(max((d - 0.35) / 0.65, 0), 1) ** 1.6
            row.append("#%02x%02x%02x" % tuple(int(b[i] + (dark[i] - b[i]) * t) for i in range(3)))
        rows.append("{" + " ".join(row) + "}")
    small = tk.PhotoImage(width=sw, height=sh)
    small.put(" ".join(rows))
    return small.zoom(step, step)


ICON_COLORS = {  # tile, symbol, and the list background they're blended against
    "light": {"tile": "#e3eaf5", "fg": "#2f6fbf", "bg": "#fafafa"},
    "dark":  {"tile": "#2c3442", "fg": "#79b0ff", "bg": "#1c1c1c"},
}


def make_icon(kind: str, w: int, h: int, theme: str) -> tk.PhotoImage:
    """Draw a list icon in pure Tk (no image files): 'audio' (music note), 'video' (play
    symbol, used when a frame can't be grabbed) or 'pending' (blank tile while loading)."""
    c = ICON_COLORS[theme]

    def rgb(hx):
        return tuple(int(hx[i:i + 2], 16) for i in (1, 3, 5))

    tile, fg, bg = rgb(c["tile"]), rgb(c["fg"]), rgb(c["bg"])
    r = h * 0.14  # tile corner radius

    def in_tile(x, y):
        dx = max(r - x, 0, x - (w - r))
        dy = max(r - y, 0, y - (h - r))
        return dx * dx + dy * dy <= r * r

    def in_symbol(x, y):
        if kind == "audio":  # eighth note
            cx, cy, rx, ry = w * 0.47, h * 0.70, h * 0.13, h * 0.10
            if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1:
                return True
            sx1, sx0 = cx + rx, cx + rx - h * 0.055
            top = h * 0.20
            if sx0 <= x <= sx1 and top <= y <= cy:
                return True
            if sx1 <= x <= sx1 + h * 0.20:  # flag
                t = (x - sx1) / (h * 0.20)
                y0 = top + t * h * 0.16
                return y0 <= y <= y0 + h * 0.09
            return False
        if kind == "video":  # play triangle
            cx, cy, s = w * 0.5, h * 0.5, h * 0.22
            return cx - s * 0.8 <= x and abs(y - cy) <= (cx + s - x) * 0.62
        if kind == "doc":  # page with text lines (a transcript whose video isn't around anymore)
            x0, x1, y0, y1 = w * 0.5 - h * 0.21, w * 0.5 + h * 0.21, h * 0.15, h * 0.85
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                return False
            if x0 + h * 0.08 <= x <= x1 - h * 0.08:
                for top in (0.32, 0.46, 0.60):
                    if h * top <= y <= h * (top + 0.06):
                        return False
            return True
        return False

    img = tk.PhotoImage(width=w, height=h)
    offs = (1 / 6, 1 / 2, 5 / 6)  # 3x3 supersampling for smooth edges
    rows = []
    for py in range(h):
        row = []
        for px in range(w):
            acc = [0.0, 0.0, 0.0]
            for oy in offs:
                for ox in offs:
                    x, y = px + ox, py + oy
                    col = (fg if in_symbol(x, y) else tile) if in_tile(x, y) else bg
                    acc[0] += col[0]; acc[1] += col[1]; acc[2] += col[2]
            row.append("#%02x%02x%02x" % tuple(int(v / 9) for v in acc))
        rows.append("{" + " ".join(row) + "}")
    img.put(" ".join(rows))
    return img


def system_theme() -> str:
    """'dark' or 'light', following the Windows 'Choose your app mode' setting."""
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            light, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return "light" if light else "dark"
        except OSError:
            return "light"
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True,
                                 text=True, timeout=2).stdout
            return "dark" if "dark" in out.lower() else "light"
        except Exception:
            return "light"
    try:  # Linux, if darkdetect happens to be installed
        import darkdetect
        return "dark" if darkdetect.isDark() else "light"
    except Exception:
        return "light"


def set_dark_titlebar(window: tk.Tk, dark: bool) -> None:
    """Match the Windows title bar to the theme (Windows 10 build 1809+ and Windows 11)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (20 on newer builds, 19 on older)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
        # Ask Windows to repaint the frame now instead of on the next focus change.
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)
    except Exception:
        pass


def short_length(seconds) -> str:
    """3:46 or 1:02:05 style length for the file list."""
    if not seconds:
        return "?"
    total = max(round(seconds), 1)
    h, rem = divmod(total, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def friendly_date(ts: float) -> str:
    """'10:31 PM' (today), 'Yesterday 3:12 PM', 'Mon 3:12 PM' (this past week), 'Oct 24' (this year)
    or 'Oct 24, 2025'."""
    d = time.localtime(ts)
    clock = time.strftime("%I:%M %p", d).lstrip("0")
    today = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))   # midnight this morning
    day = time.mktime(d[:3] + (0, 0, 0, 0, 0, -1))
    days_ago = round((today - day) / 86400)
    if days_ago <= 0:
        return clock
    if days_ago == 1:
        return f"Yesterday {clock}"
    if days_ago < 7:
        return f"{time.strftime('%a', d)} {clock}"
    month_day = f"{time.strftime('%b', d)} {d.tm_mday}"
    return month_day if d.tm_year == time.localtime().tm_year else f"{month_day}, {d.tm_year}"


def is_network_path(path) -> bool:
    """True for mapped network drives (like a NAS on Y:) and \\\\server\\share paths."""
    p = str(path)
    if p.startswith("\\\\"):
        return True
    if sys.platform == "win32" and len(p) >= 2 and p[1] == ":":
        try:
            import ctypes
            return ctypes.windll.kernel32.GetDriveTypeW(p[:2] + "\\") == 4  # DRIVE_REMOTE
        except Exception:
            return False
    return False


def move_to_recycle_bin(paths: list[Path]) -> bool:
    """Send files to the Recycle Bin (Windows). Elsewhere, or if that fails, delete them.
    Returns True if every file is gone afterward."""
    paths = [Path(p) for p in paths if Path(p).exists()]
    if not paths:
        return True
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class SHFILEOPSTRUCTW(ctypes.Structure):
                _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                            ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                            ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                            ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

            op = SHFILEOPSTRUCTW()
            op.wFunc = 3                                    # FO_DELETE
            op.pFrom = "\0".join(os.path.abspath(p) for p in paths) + "\0\0"
            op.fFlags = 0x40 | 0x10 | 0x4 | 0x400           # ALLOWUNDO, NOCONFIRMATION, SILENT, NOERRORUI
            ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        except Exception:
            pass
    elif sys.platform == "darwin":                      # the Trash, with a new name if one is taken
        import shutil
        trash = Path.home() / ".Trash"
        for p in paths:
            try:
                dest = trash / p.name
                n = 2
                while dest.exists():
                    dest = trash / f"{p.stem} {n}{p.suffix}"
                    n += 1
                shutil.move(str(p), str(dest))
            except OSError:
                pass
    for p in paths:  # anything still there (Linux, or the Recycle Bin/Trash refused it)
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass
    return not any(p.exists() for p in paths)


def point_on_a_monitor(x: int, y: int) -> bool:
    """True if this screen point is on a connected monitor (so a remembered position is still usable)."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes
        return bool(ctypes.windll.user32.MonitorFromPoint(wintypes.POINT(x, y), 0))  # 0 = MONITOR_DEFAULTTONULL
    except Exception:
        return True


_GEOMETRY = re.compile(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$")


def show_in_folder(path: Path) -> None:
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])             # Finder, with the file selected
    else:
        open_path(path.parent)


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_PATH)
    except OSError:
        pass


def open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
