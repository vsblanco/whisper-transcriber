"""Recording one app's sound (Chrome, Discord, a game) instead of everything the PC plays, with
Windows' per-app loopback (Windows 10 version 2004 or newer, Windows 11), through the proc-tap
package (pip install proc-tap). It includes the app's helper processes, which is where browsers
play their sound from. A browser is recorded as a whole (all its tabs), not one tab.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

MIN_BUILD = 19041          # Windows 10 version 2004 ("20H1"): the first with per-app loopback


def available() -> tuple[bool, str]:
    """(ok, why not)."""
    if sys.platform != "win32":
        return False, "Recording one app is Windows only (Windows 10 version 2004 or newer)."
    try:
        if sys.getwindowsversion().build < MIN_BUILD:
            return False, "Recording one app needs Windows 10 version 2004 or newer."
    except Exception:
        pass
    import importlib
    import importlib.util
    importlib.invalidate_caches()                 # (installed a moment ago from What's installed)
    if importlib.util.find_spec("proctap") is None:
        return False, "Recording one app needs the proc-tap package (What's installed > App audio)."
    return True, ""


def _exe_name(pid: int) -> str:
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        k32.CloseHandle(h)


def friendly(exe: str) -> str:
    """'chrome.exe' -> 'Chrome' (a track name)."""
    base = os.path.splitext(exe)[0]
    known = {"msedge": "Edge", "firefox": "Firefox", "chrome": "Chrome", "discord": "Discord", "spotify": "Spotify",
             "vlc": "VLC", "zoom": "Zoom", "ms-teams": "Teams", "teams": "Teams", "obs64": "OBS", "brave": "Brave",
             "opera": "Opera", "steam": "Steam", "steamwebhelper": "Steam"}
    return known.get(base.lower(), base[:1].upper() + base[1:] if base else "App")


def windows() -> list[dict]:
    """The open windows with a title: [{"pid", "title", "exe", "name"}], one per window, this app's
    own windows left out."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found, me = [], os.getpid()
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):     # (GW_OWNER: popups)
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n == 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            title = buf.value.strip()
            if pid.value and pid.value != me and title and title not in ("Program Manager", "Settings"):
                exe = _exe_name(pid.value)
                if exe.lower() not in ("explorer.exe", "textinputhost.exe", "applicationframehost.exe",
                                       "shellexperiencehost.exe", "searchhost.exe", "startmenuexperiencehost.exe"):
                    found.append({"pid": pid.value, "title": title, "exe": exe, "name": friendly(exe)})
        except Exception:
            pass
        return True
    user32.EnumWindows(proto(each), 0)
    return found


def label(win: dict, width: int = 60) -> str:
    """What the list shows: 'Chrome: Lofi beats - YouTube'."""
    text = f"{win['name']}: {win['title']}" if win["title"].lower() != win["name"].lower() else win["name"]
    return text if len(text) <= width else text[: width - 1] + "…"


def find(wins: list[dict], exe: str, title: str = "") -> Optional[dict]:
    """The window to preselect: the same title, else the same app."""
    exe = (exe or "").lower()
    return (next((w for w in wins if w["exe"].lower() == exe and w["title"] == title), None) or
            next((w for w in wins if w["exe"].lower() == exe), None))
