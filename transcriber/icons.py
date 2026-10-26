"""Small icon buttons using Windows' built-in icon font, with hover tooltips.

Windows 11 has "Segoe Fluent Icons" and Windows 10 has "Segoe MDL2 Assets"; both share these
code points. On systems without either font, buttons fall back to short text labels.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from transcriber.fonts import ui_font

# name: (icon-font glyph, fallback text)
GLYPHS = {
    "folder": ("", "Folder"),
    "refresh": ("", "↻"),
    "delete": ("", "Delete"),
    "play": ("", "▶"),
    "pause": ("", "⏸"),
    "volume": ("", "Vol"),
    "mute": ("", "Mute"),
    "settings": ("", "⚙"),
    "mic": ("", "Mic"),
    "info": ("", "ⓘ"),
    "stats": ("", "Talk time"),
    "history": ("", "History"),
    "person": ("", "Speakers"),
    "audioview": ("\ue8d6", "Audio"),
    "videoview": ("\ue714", "Video"),
    "export": ("\ue72d", "Export"),
    "fullscreen": ("", "⛶"),
    "exitfull": ("", "✕"),
}

_font_family: str | None | bool = False   # False = not checked yet


def icon_family(root) -> str | None:
    global _font_family
    if _font_family is False:
        families = set(tkfont.families(root))
        _font_family = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in families), None)
    return _font_family


def glyph(root, name: str) -> str:
    g, fallback = GLYPHS[name]
    return g if icon_family(root) else fallback


def setup_styles(root) -> None:
    """Define Icon.TButton (call again after each theme switch; styles belong to the theme)."""
    fam = icon_family(root)
    style = ttk.Style(root)
    if fam:
        style.configure("Icon.TButton", font=(fam, 11), padding=(8, 4))
    else:
        style.configure("Icon.TButton", padding=(6, 4))


def icon_button(parent, root, name: str, tip: str, command) -> ttk.Button:
    b = ttk.Button(parent, text=glyph(root, name), style="Icon.TButton", command=command, width=3 if icon_family(root) else 0)
    Tooltip(b, tip)
    return b


def info_icon(parent, root, tip: str) -> ttk.Label:
    """A small (i) that explains something when you hover over it."""
    fam = icon_family(root)
    lbl = ttk.Label(parent, text=glyph(root, "info"), style="Muted.TLabel",
                    font=(fam, 9) if fam else ui_font(10), cursor="question_arrow")
    Tooltip(lbl, tip, delay=150)
    return lbl


class Tooltip:
    """Shows a small label under a widget after hovering for half a second."""

    def __init__(self, widget, text: str, delay: int = 500):
        self.widget, self.text, self.delay = widget, text, delay
        self.tip = None
        self._job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def set_text(self, text: str):
        self.text = text

    def _schedule(self, _e=None):
        self._cancel()
        self._job = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._job:
            self.widget.after_cancel(self._job)
            self._job = None

    def _show(self):
        if self.tip or not self.widget.winfo_exists():
            return
        dark = str(ttk.Style(self.widget).lookup("TFrame", "background")).lower() in ("#1c1c1c", "#202020", "#2b2b2b")
        bg, fg, border = ("#2b2b2b", "#f2f2f2", "#454545") if dark else ("#ffffff", "#1c1c1c", "#cfcfcf")
        self.tip = tk.Toplevel(self.widget)
        self.tip.overrideredirect(True)
        self.tip.configure(background=border, padx=1, pady=1)
        tk.Label(self.tip, text=self.text, background=bg, foreground=fg, font=ui_font(9),
                 padx=8, pady=4, wraplength=280, justify="left").pack()
        self.tip.update_idletasks()
        x = self.widget.winfo_rootx() + (self.widget.winfo_width() - self.tip.winfo_reqwidth()) // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip.geometry(f"+{x}+{y}")

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None
