"""Fonts, mouse buttons and keyboard shortcuts that differ between Windows and macOS.

No imports from the rest of the app, so any module can use it."""

from __future__ import annotations

import sys

MAC = sys.platform == "darwin"
WINDOWS = sys.platform == "win32"

if WINDOWS:
    UI, UI_BOLD, BOLD_WEIGHT = "Segoe UI", "Segoe UI Semibold", None
elif MAC:
    UI, UI_BOLD, BOLD_WEIGHT = "Helvetica Neue", "Helvetica Neue", "bold"
else:
    UI, UI_BOLD, BOLD_WEIGHT = "DejaVu Sans", "DejaVu Sans", "bold"

# Font name for text drawn by other programs (VLC captions, burned-in clip captions).
CAPTION_FONT = "Segoe UI Semibold" if WINDOWS else "Helvetica Neue" if MAC else "DejaVu Sans"

# Tk on macOS counts a point as one screen pixel (Windows: 96/72), so sizes look small there.
SIZE = 1.3 if MAC else 1.0

# The right mouse button is Button-3 on Windows/Linux but Button-2 in Tk on macOS.
RIGHT_CLICK = "<Button-2>" if MAC else "<Button-3>"

# Shortcuts use Command on a Mac and Control elsewhere.
MODS = ("Control", "Command") if MAC else ("Control",)

# How to install a missing package, as shown in messages.
PIP = "python3 -m pip" if not WINDOWS else "py -3.12 -m pip"


def ui_font(size, style=None, bold: bool = False) -> tuple:
    """("Segoe UI", 10) on Windows; the same look and size on a Mac."""
    size = int(round(size * SIZE))
    family = UI_BOLD if bold else UI
    parts = [family, size]
    if bold and BOLD_WEIGHT:
        parts.append(BOLD_WEIGHT)
    if style:
        parts.append(style)
    return tuple(parts)


def bind_keys(widget, key: str, fn, add=None) -> None:
    """Bind Ctrl+key (and Cmd+key on a Mac). key like 'z', 'Z', 'Return', 'Button-1'."""
    for mod in MODS:
        widget.bind(f"<{mod}-{key}>", fn, add=add)
