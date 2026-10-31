"""Fix a transcript's wording right where you read it.

Double-click a row to edit its text in place (Enter or Esc finishes). Changed characters get a
subtle yellow highlight, and a bar fades in under the transcript with Discard, Undo, Redo and
Save. Edits wait in memory until you save, so you can switch transcripts and come back.

Used as a mixin by the App: it expects self.text (the transcript Text widget), self.right (its
panel), self.people, PALETTE colors via self._edit_colors(), and the App's _render() calling
_edit_before_render() / _edit_after_render().
"""

from __future__ import annotations

import difflib
from pathlib import Path
import re
import time
import tkinter as tk
from tkinter import messagebox

from typing import Optional

from transcriber.fonts import bind_keys, ui_font
from transcriber.core import write_text_safe
from transcriber.icons import Tooltip, icon_family

EDITED_BG = {"light": "#fff1b8", "dark": "#4d4417"}
DELETED_BG = {"light": "#fde0e0", "dark": "#4a1f23"}
ACCENT = {"light": ("#005fb8", "#ffffff", "#1a6fc4"), "dark": ("#57c8ff", "#000000", "#79d4ff")}
UNDO, REDO = "", ""


def _mix(a: str, b: str, t: float) -> str:
    """Blend two #rrggbb colors (t=0 gives a, t=1 gives b)."""
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


class EditingMixin:

    # ------------------------------------------------------------ setup

    def _edit_setup(self):
        self._edits: dict[tuple, Optional[str]] = {}   # (file key, line index) -> new text, or None = delete
        self._edit_files: dict[str, dict] = {}    # file key -> that version's files
        self._hist: list[dict] = [{}]             # undo history of self._edits
        self._hist_i = 0
        self._last_edit = (None, 0.0)
        self._editing = None                      # text line being edited
        self._row_meta: dict[int, dict] = {}      # text line -> {"key", "orig", "files", "fkey"}
        self._resume_edit = None

        t = self.text
        t.tag_configure("editrow")
        t.bind("<Double-Button-1>", self._on_text_double)
        t.bind("<Key>", self._edit_key)
        for ev, kind in (("<<Paste>>", "paste"), ("<<Cut>>", "cut"), ("<<Clear>>", "clear"),
                         ("<<PasteSelection>>", "none")):
            t.bind(ev, lambda e, kind=kind: self._edit_virtual(kind))
        t.bind("<ButtonRelease-1>", lambda e: self.after_idle(self._clamp_insert), add="+")
        def undo_or_redo(e):          # Ctrl+Shift+Z redoes; plain Ctrl+Z (even with Caps Lock on) undoes
            return self._edit_redo() if e.state & 0x1 else self._edit_undo()
        for key, fn in (("z", undo_or_redo), ("y", lambda e: self._edit_redo()),
                        ("Z", undo_or_redo), ("s", lambda e: self._edit_save())):
            bind_keys(self, key, lambda e, fn=fn: (fn(e) if self._edits or self._hist_i or self._split_undo else None))
        self._split_undo: list = []
        bind_keys(t, "x", self._on_split_key)
        t.bind("<Control-Button-1>", self._on_ctrl_click)
        bind_keys(t, "X", self._on_split_key)

        # The bar under the transcript (only while there are unsaved edits).
        bar = tk.Frame(self.right, highlightthickness=1, bd=0)
        self.edit_bar = bar
        self._bar_shown = False
        self._fade_job = None
        fam = icon_family(self)

        def button(text, command, font, padx=12):
            b = tk.Label(bar, text=text, font=font, padx=padx, pady=4, cursor="hand2")
            b.bind("<Button-1>", lambda e: command() if b.cget("cursor") == "hand2" else None)
            b.bind("<Enter>", lambda e: self._paint_edit_bar(1.0, hover=b))
            b.bind("<Leave>", lambda e: self._paint_edit_bar(1.0))
            return b

        self._bar_discard = button("Discard", self._edit_discard, ui_font(10))
        self._bar_discard.pack(side="left", padx=(6, 0), pady=6)
        self._bar_count = tk.Label(bar, font=ui_font(9))
        self._bar_count.pack(side="left", padx=8)
        self._bar_save = button("Save", self._edit_save, ui_font(10, bold=True), padx=16)
        self._bar_save.pack(side="right", padx=(4, 8), pady=6)
        icon_font = (fam, 11) if fam else ui_font(10)
        self._bar_redo = button(REDO if fam else "Redo", self._edit_redo, icon_font, padx=9)
        self._bar_redo.pack(side="right", pady=6)
        self._bar_undo = button(UNDO if fam else "Undo", self._edit_undo, icon_font, padx=9)
        self._bar_undo.pack(side="right", pady=6)
        Tooltip(self._bar_undo, "Undo (Ctrl+Z)")
        Tooltip(self._bar_redo, "Redo (Ctrl+Y)")
        Tooltip(self._bar_save, "Save changes to the transcript files (Ctrl+S)")
        self._edit_theme()

    def _edit_colors(self) -> dict:
        mode = getattr(self, "current_theme", None) or "light"
        from tkinter import ttk
        frame_bg = str(ttk.Style(self).lookup("TFrame", "background") or "")
        if not str(frame_bg).startswith("#") or len(str(frame_bg)) != 7:
            frame_bg = "#fafafa" if mode == "light" else "#1c1c1c"
        p = self._palette()
        acc, acc_fg, acc_hover = ACCENT[mode]
        return {"frame": frame_bg, "bar": p["band"], "border": "#d9d9d9" if mode == "light" else "#3a3a3a",
                "fg": p["fg"], "muted": p["muted"], "hover": p["hover"], "acc": acc, "acc_fg": acc_fg,
                "acc_hover": acc_hover, "edited": EDITED_BG[mode], "deleted": DELETED_BG[mode]}

    def _edit_theme(self):
        """Called after a theme change."""
        self.text.tag_configure("edited", background=self._edit_colors()["edited"])
        self.text.tag_configure("editrow", background=self._edit_colors()["hover"])
        self.text.tag_configure("deleted", background=self._edit_colors()["deleted"],
                                foreground=self._palette()["muted"], overstrike=True)
        for tag in ("editrow", "edited", "deleted"):
            self.text.tag_raise(tag)          # above the row shading and the player's blue row
        self.text.tag_raise("sel")
        if self._bar_shown:
            self._paint_edit_bar(1.0)

    # ------------------------------------------------------------ render hooks

    def _edit_before_render(self):
        """Remember where an edit was going on, so a refresh doesn't kick you out of it."""
        self._end_time_edit(commit=False)
        self._resume_edit = None
        if self._editing is not None and self._editing in self._row_meta:
            meta = self._row_meta[self._editing]
            try:
                offset = len(self.text.get(f"b{self._editing}s", "insert"))
            except tk.TclError:
                offset = 0
            self._resume_edit = (meta["key"], offset)
        self._editing = None
        self._row_meta = {}
        # Take the rows' marks out BEFORE the text is cleared and redrawn. Deleting the text leaves
        # every mark piled up at the start, and Tk then wades through that pile on each insert: with
        # a few thousand rows, a redraw went from a quarter of a second to minutes.
        t = self.text
        for m in [m for m in t.mark_names() if m.startswith("b") and m[1:-1].isdigit()]:
            t.mark_unset(m)

    def _edit_text_for(self, meta) -> str:
        fkey = self._file_key(meta["files"])
        meta["fkey"] = fkey
        meta["key"] = (fkey, meta["i"])
        self._edit_files[fkey] = meta["files"]
        text = self._edits.get(meta["key"], meta["orig"])
        return meta["orig"] if text is None else text

    def _is_deleted(self, meta) -> bool:
        return meta["key"] in self._edits and self._edits[meta["key"]] is None

    def _mark_deleted(self, line: int, on: bool):
        if on:
            self.text.tag_add("deleted", f"{line}.0", f"{line}.0 lineend +1c")
        else:
            self.text.tag_remove("deleted", f"{line}.0", f"{line}.0 lineend +1c")

    def _edit_after_render(self, rows: dict):
        """rows = {line: (meta, col0, col1)} for the editable rows just drawn."""
        t = self.text
        resume = None
        for line, (meta, c0, c1) in rows.items():
            t.mark_set(f"b{line}s", f"{line}.{c0}")
            t.mark_gravity(f"b{line}s", "left")
            t.mark_set(f"b{line}e", f"{line}.{c1}")
            t.mark_gravity(f"b{line}e", "right")
            self._row_meta[line] = meta
            if self._is_deleted(meta):
                self._mark_deleted(line, True)
            elif meta["key"] in self._edits:
                self._highlight(line)
            if self._time_key(meta) in self._edits:
                self._show_time(line)
            if self._resume_edit and meta["key"] == self._resume_edit[0]:
                resume = (line, self._resume_edit[1])
        if resume:
            line, offset = resume
            self.after_idle(lambda: self._start_edit(line, f"b{line}s + {offset} chars"))
        self._applied = dict(self._edits)             # what the rows show now

    @staticmethod
    def _file_key(files: dict) -> str:
        return str(files.get("speakers") or files.get("txt") or files.get("srt") or files.get("vtt"))

    # ------------------------------------------------------------ starting and ending an edit

    def _on_text_double(self, e):
        idx = self.text.index(f"@{e.x},{e.y}")
        line = int(idx.split(".")[0])
        if "tsend" in self.text.tag_names(f"{line}.0"):          # a folded paragraph's second line
            line -= 1
        info = getattr(self, "_group_lines", {}).get(line)
        if info and not info["open"] and "who" not in self.text.tag_names(idx):
            self._toggle_paragraph(line, True)       # a folded paragraph opens into its lines
            return "break"
        if line not in self._row_meta or "who" in self.text.tag_names(idx):
            return None
        if "ts" in self.text.tag_names(idx):
            self._start_time_edit(line)
            return "break"
        self._start_edit(line, idx)
        return "break"

    # ------------------------------------------------------------ changing a line's time

    @staticmethod
    def _parse_time(text: str):
        """'1:02:03', '2:03', '123' or '2:03.5' -> seconds, or None if it isn't a time."""
        text = text.strip().strip("[]")
        try:
            parts = [float(x) for x in text.split(":")]
        except ValueError:
            return None
        if not parts or len(parts) > 3 or any(x < 0 for x in parts):
            return None
        sec = 0.0
        for x in parts:
            sec = sec * 60 + x
        return sec

    def _ts_len(self, line: int) -> int:
        """How many characters the row's timestamp has, inside its brackets."""
        import re
        m = re.match(r"\[([0-9:.]+)\]", self.text.get(f"{line}.0", f"{line}.20"))
        return len(m[1]) if m else 8

    def _time_key(self, meta):
        return meta["key"] + ("t",)

    def _layer_key(self, meta):
        """The pending-edit key for a line's caption spot (the timeline's stacking)."""
        return meta["key"] + ("layer",)

    def _shown_time(self, meta) -> float:
        t = self._edits.get(self._time_key(meta), meta.get("sec", 0.0))
        return t[0] if isinstance(t, (tuple, list)) else t     # (start, end) from the timeline

    def _show_time(self, line: int):
        """Put a row's current time (edited or not) into its timestamp, highlighted if changed."""
        meta, t = self._row_meta.get(line), self.text
        if not meta or "sec" not in meta:
            return
        new = self._ts(self._shown_time(meta))
        a, b = f"{line}.1", f"{line}.{1 + self._ts_len(line)}"
        if t.get(a, b) != new:
            state = t.cget("state")
            t.configure(state="normal")
            t.delete(a, b)
            t.insert(a, new, ("ts",) + tuple(x for x in t.tag_names(f"{line}.0") if x in ("band", "hover", "now", "mutedrow")))
            t.configure(state=state)
        t.tag_remove("edited", a, b)
        if self._time_key(meta) in self._edits:
            t.tag_add("edited", a, b)

    def _start_time_edit(self, line: int):
        meta = self._row_meta.get(line)
        if not meta or self._is_deleted(meta) or "sec" not in meta:
            return
        self._end_edit()
        self._end_time_edit(commit=False)
        t = self.text
        box = t.bbox(f"{line}.0")
        end = t.bbox(f"{line}.{1 + self._ts_len(line)}")
        if not box or not end:
            return
        x, y, _w, h = box
        width = max(end[0] + end[2] - x - 6, int(t.tk.call("font", "measure", ("Consolas", 9), "00:00:00.000")) + 4)
        p = self._palette()
        entry = tk.Entry(t, font=("Consolas", 9), relief="flat", borderwidth=0, highlightthickness=1,
                         background=p["bg"], foreground=p["fg"], insertbackground=p["fg"],
                         highlightcolor=self._edit_colors()["acc"], highlightbackground=self._edit_colors()["acc"],
                         justify="center")
        from transcriber.core import format_timestamp
        entry.insert(0, format_timestamp(self._shown_time(meta), millis=True, sep="."))     # (exact, to the ms)
        entry.place(x=x, y=y - 1, width=width + 6, height=h + 2)
        entry.select_range(0, "end")
        entry.focus_set()
        self._time_entry = (entry, line)
        entry.bind("<Return>", lambda e: self._end_time_edit(commit=True) or "break")
        entry.bind("<KP_Enter>", lambda e: self._end_time_edit(commit=True) or "break")
        entry.bind("<Escape>", lambda e: self._end_time_edit(commit=False) or "break")
        entry.bind("<FocusOut>", lambda e: self._end_time_edit(commit=True))

    def _end_time_edit(self, commit: bool):
        current = getattr(self, "_time_entry", None)
        if not current:
            return
        entry, line = current
        self._time_entry = None
        value = entry.get()
        entry.destroy()
        self.text.focus_set()
        meta = self._row_meta.get(line)
        if not commit or not meta:
            return
        sec = self._parse_time(value)
        if sec is None:
            self.bell()
            return
        new = dict(self._edits)
        key = self._time_key(meta)
        old = new.get(key)
        if abs(sec - float(meta["sec"])) < 0.0005 and not isinstance(old, (tuple, list)):
            new.pop(key, None)
        elif isinstance(old, (tuple, list)):             # set on the timeline: keep its length
            new[key] = (sec, sec + (old[1] - old[0]))
        else:
            new[key] = sec
        if new != self._edits:
            self._push_state(new)
            self._apply_edit_state(new)

    def _start_edit(self, line: int, index: str):
        if line not in self._row_meta or self._is_deleted(self._row_meta[line]):
            return
        if hasattr(self, "_show_tab"):
            self._show_tab("transcript")           # (started from the timeline or the video)
        if self._editing != line:
            self._end_edit()
        t = self.text
        self._editing = line
        t.configure(state="normal", cursor="xterm")
        t.tag_remove("editrow", "1.0", "end")
        t.tag_add("editrow", f"{line}.0", f"{line}.0 lineend +1c")
        t.tag_remove("sel", "1.0", "end")
        t.mark_set("insert", index)
        self._clamp_insert()
        self._last_edit = (None, 0.0)
        t.focus_set()

    def _end_edit(self):
        if self._editing is None:
            return
        self._edit_changed()
        t = self.text
        t.tag_remove("editrow", "1.0", "end")
        t.tag_remove("sel", "1.0", "end")
        self._editing = None
        t.configure(state="disabled", cursor="")
        self.focus_set()
        if getattr(self, "_typing_job", None) is not None:      # the edit is done: catch up now
            self.after_cancel(self._typing_job)
            self._after_typing()

    def _clamp_insert(self):
        line = self._editing
        if line is None:
            return
        t = self.text
        s, e = f"b{line}s", f"b{line}e"
        if t.compare("insert", "<", s):
            t.mark_set("insert", s)
        elif t.compare("insert", ">", e):
            t.mark_set("insert", e)

    def _in_edit_range(self, index) -> bool:
        line = self._editing
        return (self.text.compare(index, ">=", f"b{line}s") and
                self.text.compare(index, "<=", f"b{line}e"))

    # ------------------------------------------------------------ keys

    def _edit_key(self, e):
        if self._editing is None:
            return None
        t, line = self.text, self._editing
        s, en = f"b{line}s", f"b{line}e"
        ks = e.keysym
        ctrl, shift = bool(e.state & 0x4), bool(e.state & 0x1)
        sel = t.tag_ranges("sel")
        sel_ok = not sel or (self._in_edit_range(sel[0]) and self._in_edit_range(sel[1]))

        if ks in ("Return", "KP_Enter", "Escape"):
            self._end_edit()
            return "break"
        if ks in ("Tab", "ISO_Left_Tab", "Insert", "Up", "Down", "Prior", "Next"):
            return "break"
        if ctrl:
            k = ks.lower()
            if k == "z":
                self._edit_redo() if shift else self._edit_undo()
            elif k == "y":
                self._edit_redo()
            elif k == "s":
                self._edit_save()
            elif k == "f" and hasattr(self, "_find_open"):
                self._find_open()
            elif k == "h" and hasattr(self, "_find_open"):
                self._find_open(replace=True)
            elif k == "v":
                return self._edit_virtual("paste")
            elif k == "x":
                return self._on_split_key()
            elif k == "a":
                t.tag_remove("sel", "1.0", "end")
                t.tag_add("sel", s, en)
                t.mark_set("insert", en)
            elif k in ("c", "left", "right"):
                self.after_idle(self._clamp_insert)
                return None
            return "break"
        if ks in ("Home", "End"):
            target = s if ks == "Home" else en
            if shift:
                t.tag_remove("sel", "1.0", "end")
                a, b = sorted([t.index("insert"), t.index(target)], key=lambda i: tuple(map(int, i.split("."))))
                t.tag_add("sel", a, b)
            t.mark_set("insert", target)
            return "break"
        if ks == "Left" and t.compare("insert", "<=", s) and not (sel and not shift):
            return "break"
        if ks == "Right" and t.compare("insert", ">=", en) and not (sel and not shift):
            return "break"
        if ks in ("Left", "Right"):
            self.after_idle(self._clamp_insert)
            return None
        if ks == "BackSpace":
            if not sel_ok or (not sel and t.compare("insert", "<=", s)):
                return "break"
            self.after_idle(self._edit_changed)
            return None
        if ks == "Delete":
            if not sel_ok or (not sel and t.compare("insert", ">=", en)):
                return "break"
            self.after_idle(self._edit_changed)
            return None
        if e.char and ord(e.char[0]) >= 32 and e.char not in "\r\n\t":
            if not sel_ok or not self._in_edit_range("insert"):
                return "break"
            self.after_idle(self._edit_changed)
            return None
        return None

    def _edit_virtual(self, kind: str):
        """Paste (as one line), cut and clear, limited to the row being edited."""
        if self._editing is None:
            return "break"
        t = self.text
        sel = t.tag_ranges("sel")
        sel_ok = not sel or (self._in_edit_range(sel[0]) and self._in_edit_range(sel[1]))
        if not sel_ok:
            return "break"
        if kind == "paste":
            try:
                clip = " ".join(self.clipboard_get().split())
            except tk.TclError:
                return "break"
            if sel:
                t.delete(sel[0], sel[1])
            if self._in_edit_range("insert") and clip:
                t.insert("insert", clip)
        elif kind == "cut" and sel:
            self.clipboard_clear()
            self.clipboard_append(t.get(sel[0], sel[1]))
            t.delete(sel[0], sel[1])
        elif kind == "clear" and sel:
            t.delete(sel[0], sel[1])
        self._edit_changed()
        return "break"

    # ------------------------------------------------------------ changes, highlight, history

    def _edit_changed(self):
        line = self._editing
        meta = self._row_meta.get(line) if line is not None else None
        if not meta:
            return
        cur = self.text.get(f"b{line}s", f"b{line}e")
        new = dict(self._edits)
        if " ".join(cur.split()) != " ".join(meta["orig"].split()):
            new[meta["key"]] = cur
        else:
            new.pop(meta["key"], None)
        self._highlight(line)
        if new == self._edits:
            return
        applied = getattr(self, "_applied", None)
        if applied is not None:                        # this row shows the typed text already
            if meta["key"] in new:
                applied[meta["key"]] = new[meta["key"]]
            else:
                applied.pop(meta["key"], None)
        self._push_state(new, coalesce_key=meta["key"], text=cur)

    def _push_state(self, new: dict, coalesce_key=None, text: str = ""):
        """Record a change in the undo history. A burst of typing in one line is one step."""
        now = time.time()
        same_burst = (coalesce_key is not None and self._last_edit[0] == coalesce_key
                      and now - self._last_edit[1] < 1.2 and not text.endswith(" ")
                      and self._hist_i == len(self._hist) - 1 and self._hist_i > 0)
        if same_burst:
            self._hist[self._hist_i] = new
        else:
            del self._hist[self._hist_i + 1:]
            self._hist.append(new)
            self._hist_i += 1
        self._edits = new
        self._last_edit = (coalesce_key, now)
        self._update_edit_bar()

    def _toggle_delete(self, line: int):
        """Mark a line to be removed when you save (or bring it back)."""
        meta = self._row_meta.get(line)
        if not meta:
            return
        if self._editing is not None:
            self._end_edit()
        new = dict(self._edits)
        if self._is_deleted(meta):
            new.pop(meta["key"], None)
        else:
            new[meta["key"]] = None
        self._push_state(new)
        self._apply_edit_state(new)

    def _highlight(self, line: int):
        """Yellow on exactly the characters that differ from the saved text."""
        t, meta = self.text, self._row_meta.get(line)
        if not meta:
            return
        s, e = f"b{line}s", f"b{line}e"
        t.tag_remove("edited", f"{line}.0", f"{line}.0 lineend")
        cur, orig = t.get(s, e), meta["orig"]
        if cur == orig or self._is_deleted(meta):
            return
        for op, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, orig, cur, autojunk=False).get_opcodes():
            if op in ("replace", "insert"):
                t.tag_add("edited", f"{s} + {j1} chars", f"{s} + {j2} chars")
            elif op == "delete" and cur:         # removed text: mark the spot with the nearest letter
                a = j1 if j1 < len(cur) else j1 - 1
                t.tag_add("edited", f"{s} + {a} chars", f"{s} + {a + 1} chars")

    def _apply_edit_state(self, state: dict, full: bool = False):
        """Show a history state: put each visible row's text back to what that state says. Only the
        rows whose edits differ from the current state are touched (thousands of rows, one change);
        full: every row."""
        t = self.text
        editing = self._editing
        shown = getattr(self, "_applied", None)      # the state the rows were last drawn or edited to
        self._edits = dict(state)
        if full or shown is None:
            rows = list(self._row_meta.items())
        else:
            missing = object()
            changed = {k[:2] for k in set(shown) | set(self._edits) if shown.get(k, missing) != self._edits.get(k, missing)}
            rows = [(line, meta) for line, meta in self._row_meta.items() if meta["key"] in changed]
        self._applied = dict(self._edits)
        t.configure(state="normal")
        for line, meta in rows:
            want = self._edits.get(meta["key"], meta["orig"])
            if want is None:
                want = meta["orig"]
            self._mark_deleted(line, self._is_deleted(meta))
            s, e = f"b{line}s", f"b{line}e"
            if t.get(s, e) != want:
                t.delete(s, e)
                t.insert(s, want)
                if editing == line:
                    t.mark_set("insert", e)
            self._highlight(line)
            self._show_time(line)
        t.configure(state="normal" if editing is not None else "disabled")
        self._last_edit = (None, 0.0)
        self._update_edit_bar()

    def _edit_undo(self):
        if self._editing is not None:
            self._edit_changed()
        if self._hist_i > 0:
            self._hist_i -= 1
            self._apply_edit_state(self._hist[self._hist_i])
        elif self._split_undo:
            self._undo_split()

    # ------------------------------------------------------------ splitting a line (Ctrl+X)

    def _on_split_key(self, e=None):
        """Ctrl+X: split the highlighted line at its first sentence end, or (while editing) where
        the text cursor is. With text selected while editing, it's an ordinary cut."""
        if getattr(self, "_time_entry", None):
            return None
        t = self.text
        if self._editing is not None:
            if t.tag_ranges("sel"):
                return self._edit_virtual("cut")
            line = self._editing
            text = t.get(f"b{line}s", f"b{line}e")
            offset = len(t.get(f"b{line}s", "insert"))
        else:
            line = self._hover_line if self._hover_line in self._row_meta else None
            if line is None or self._is_deleted(self._row_meta[line]):
                return "break"
            text = t.get(f"b{line}s", f"b{line}e")
            m = re.search(r"[.!?]+[\"')\]]*\s+(?=\S)", text)
            if not m:
                self.bell()
                self.status_var.set("No period to split at. Double-click the line and put the cursor where "
                                    "it should split, then press Ctrl+X.")
                return "break"
            offset = m.end()
        left, right = text[:offset].strip(), text[offset:].strip()
        if not left or not right:
            self.bell()
            return "break"
        self._split_row(line, left, right)
        return "break"

    def _on_ctrl_click(self, e):
        """Ctrl+click a row: add a blank row under it, ready to type in."""
        self._press_index = None
        line = int(self.text.index(f"@{e.x},{e.y}").split(".")[0])
        meta = self._row_meta.get(line)
        if not meta or self._is_deleted(meta):
            return "break"
        self._end_edit()
        self._split_row(line, meta["orig"], "", start=meta.get("sec", 0.0), keep_pending=True, then_edit=True)
        return "break"

    def _split_row(self, line: int, left: str, right: str, start=None, keep_pending=False, then_edit=False):
        self._split_meta(self._row_meta[line], left, right, start, keep_pending, then_edit)

    def _split_meta(self, meta, left: str, right: str, start=None, keep_pending=False, then_edit=False,
                    new_start=None, end=None):
        """Split a line in two (saved right away; Ctrl+Z undoes it). new_start/end: exact times
        (the timeline's snip tool), otherwise worked out from the words."""
        from pathlib import Path
        from transcriber.edits import split_line
        from transcriber.speakers import rows_for
        files, i, fkey = meta["files"], meta["i"], meta["fkey"]
        if start is None:
            start = self._shown_time(meta)
        if new_start is None:
            rows = rows_for(files, self.people)
            new_start = start + 1.0                       # the second half starts a second later...
            if i + 1 < len(rows) and rows[i + 1][0] <= new_start:
                new_start = start + max((rows[i + 1][0] - start) / 2, 0)   # ...or halfway to the next line
            word_time = self._word_split_time(meta, left, right)
            if word_time is not None and right:
                new_start = word_time + (start - meta.get("sec", start))     # exactly when that word was said
        self._end_edit()
        snapshot = {}
        for f in files.values():
            try:
                snapshot[str(f)] = Path(f).read_text(encoding="utf-8")
            except OSError:
                pass
        record = {"snapshot": snapshot, "edits": dict(self._edits), "hist": list(self._hist),
                  "hist_i": self._hist_i, "where": (fkey, i)}
        # Pending changes to this line are now saved by the split; later lines move down one.
        new = {}
        for key, value in self._edits.items():
            if key[0] == fkey and key[1] == i and not keep_pending:
                continue
            if key[0] == fkey and key[1] > i:
                key = (key[0], key[1] + 1) + key[2:]
            new[key] = value
        try:
            if hasattr(self, "_backup"):
                self._backup(files)
            split_line(files, i, left, right, start, new_start, self.people, end=end)
        except Exception as exc:
            messagebox.showerror("Couldn't split the line", str(exc))
            return
        self._split_undo.append(record)
        self._edits, self._hist, self._hist_i = new, [new], 0
        self._update_edit_bar()
        self.status_var.set("Blank line added. Ctrl+Z to undo." if then_edit else "Line split. Ctrl+Z to undo.")
        self._refresh_saved(force=True)
        self._redisplay()

        def after():
            line = self._hover_key((fkey, i + 1))
            if then_edit and line:
                self._start_edit(line, f"b{line}s")
        self.after(120, after)

    def _insert_line(self, files, fkey, start: float, end: float, text: str, speaker=None) -> bool:
        """Add a line to a version (the timeline's paste). Saved right away; Ctrl+Z takes it out."""
        from pathlib import Path
        from transcriber.edits import insert_line
        from transcriber.speakers import rows_for
        self._end_edit()
        snapshot = {}
        for f in files.values():
            try:
                snapshot[str(f)] = Path(f).read_text(encoding="utf-8")
            except OSError:
                pass
        i = next((k for k, r in enumerate(rows_for(files, self.people)) if r[0] > start), None)
        record = {"snapshot": snapshot, "edits": dict(self._edits), "hist": list(self._hist),
                  "hist_i": self._hist_i, "where": (fkey, i or 0)}
        try:
            if hasattr(self, "_backup"):
                self._backup(files)
            k = insert_line(files, start, end, text, speaker, self.people)
        except Exception as exc:
            messagebox.showerror("Couldn't paste the line", str(exc))
            return False
        if k is None:
            return False
        new = {}                                     # pending changes to later lines move down one
        for key, value in self._edits.items():
            if key[0] == fkey and key[1] >= k:
                key = (key[0], key[1] + 1) + key[2:]
            new[key] = value
        self._split_undo.append(record)
        self._edits, self._hist, self._hist_i = new, [new], 0
        self._update_edit_bar()
        self._refresh_saved(force=True)
        self._redisplay()
        return True

    def _word_split_time(self, meta, left: str, right: str):
        """When the first word of the right half was said, if the line's word timings still fit."""
        wp = meta["files"].get("words")
        lines = self._words_for(wp) if wp and hasattr(self, "_words_for") else None
        if not lines or meta["i"] >= len(lines):
            return None
        ws = lines[meta["i"]]
        if " ".join(w[2].strip() for w in ws) != " ".join((left + " " + right).split()):
            return None
        n = len(left.split())
        return ws[n][0] if 0 < n < len(ws) else None

    def _undo_split(self):
        from pathlib import Path
        record = self._split_undo.pop()
        failed = []
        for path, content in record["snapshot"].items():
            try:
                write_text_safe(path, content)
            except OSError as exc:
                failed.append(f"{Path(path).name}: {exc}")
        self._edits, self._hist, self._hist_i = record["edits"], record["hist"], record["hist_i"]
        self._update_edit_bar()
        self.status_var.set("Couldn't fully undo it: " + "; ".join(failed) if failed else "Undone.")
        self._refresh_saved(force=True)
        self._redisplay()
        self.after(120, lambda: self._hover_key(record["where"]))

    def _hover_key(self, key):
        for line, meta in self._row_meta.items():
            if meta.get("key") == key:
                self._set_hover(line)
                self.text.see(f"{line}.0")
                return line
        return None

    def _edit_redo(self):
        if self._hist_i < len(self._hist) - 1:
            self._hist_i += 1
            self._apply_edit_state(self._hist[self._hist_i])

    def _edit_discard(self):
        n = len(self._edits)
        if n and not self._confirm(title="Discard changes?", heading=f"Discard {self._change_summary()}?",
                                   body="", items=[], detail="The transcript goes back to how it was saved.",
                                   confirm="Discard"):
            return
        self._end_edit()
        self._hist, self._hist_i = [{}], 0
        self._split_undo = []
        self._apply_edit_state({})

    def _edit_save(self):
        from transcriber.edits import apply_text_edits
        self._end_edit()
        if not self._edits:
            return True
        by_file: dict[str, tuple] = {}
        layers_by_file: dict[str, dict] = {}
        for key, value in self._edits.items():
            fkey, i = key[0], key[1]
            edits, deletes, times = by_file.setdefault(fkey, ({}, set(), {}))
            if len(key) > 2 and key[2] == "layer":
                layers_by_file.setdefault(fkey, {})[i] = value
            elif len(key) > 2:
                times[i] = value
            elif value is None:
                deletes.add(i)
            else:
                edits[i] = value
        failed, failed_keys = [], set()
        from transcriber.backups import new_stamp
        stamp = new_stamp()
        for fkey, (edits, deletes, times) in by_file.items():
            try:
                if hasattr(self, "_backup"):
                    self._backup(self._edit_files[fkey], stamp)
                if layers_by_file.get(fkey):
                    from transcriber.edits import set_layers
                    set_layers(self._edit_files[fkey], layers_by_file[fkey], self.people)
                apply_text_edits(self._edit_files[fkey], edits, self.people, deletes, times)
            except Exception as exc:
                failed.append(f"{Path(fkey).name}: {exc}")
                failed_keys.add(fkey)
                self._put_back(self._edit_files[fkey], stamp)   # no half-saved version
        summary = self._change_summary()
        self._split_undo = []
        kept = {k: v for k, v in self._edits.items() if k[0] in failed_keys}   # try again later
        self._edits, self._hist, self._hist_i = kept, [{}, kept] if kept else [{}], 1 if kept else 0
        self._update_edit_bar()
        if failed:
            messagebox.showerror("Couldn't save every change",
                                 "These weren't saved (your changes to them are still here, so you can "
                                 "try again):\n\n" + "\n".join(failed))
        else:
            self.status_var.set(f"Saved: {summary}.")
        self._refresh_saved(force=True)
        self._redisplay()
        return not failed

    @staticmethod
    def _put_back(files: dict, stamp: str):
        """After a failed save, put the version's files back as they were (from the copy just made)."""
        import shutil
        from transcriber.backups import _folder_for
        folder = _folder_for(files)
        if folder is None:
            return
        for f in files.values():
            src = folder / stamp / Path(f).name if f else None
            if src is not None and src.exists():
                try:
                    shutil.copy2(src, f)
                except OSError:
                    pass

    def _edits_before_close(self) -> bool:
        """Ask about unsaved edits when closing. False means don't close."""
        self._end_edit()
        if not self._edits:
            return True
        answer = messagebox.askyesnocancel("Unsaved changes",
                                           f"You have unsaved changes ({self._change_summary()}). Save them?")
        if answer is None:
            return False
        if answer:
            return bool(self._edit_save())
        return True

    # ------------------------------------------------------------ the bar

    def _change_summary(self) -> str:
        stacked = sum(1 for k in self._edits if len(k) > 2 and k[2] == "layer")
        timed = sum(1 for k in self._edits if len(k) > 2) - stacked
        edited = sum(1 for k, v in self._edits.items() if len(k) == 2 and v is not None)
        deleted = len(self._edits) - edited - timed - stacked
        parts = ([f"{edited} line{'s' if edited != 1 else ''} edited"] if edited else []) + \
                ([f"{timed} time{'s' if timed != 1 else ''} changed"] if timed else []) + \
                ([f"{stacked} caption{'s' if stacked != 1 else ''} stacked"] if stacked else []) + \
                ([f"{deleted} line{'s' if deleted != 1 else ''} deleted"] if deleted else [])
        return " \u00b7 ".join(parts)

    def _update_edit_bar(self):
        if hasattr(self, "_timeline_refresh"):
            # The timeline and the player's captions follow edits. While typing, they catch up once
            # the typing pauses (rebuilding them on every letter is what made typing stutter).
            if self._editing is not None:
                if getattr(self, "_typing_job", None) is not None:
                    self.after_cancel(self._typing_job)
                self._typing_job = self.after(350, self._after_typing)
            else:
                self._timeline_refresh()
                self._refresh_preview_caps()
        n = len(self._edits)
        self._bar_count.configure(text=self._change_summary())
        if n and not self._bar_shown:
            self._bar_shown = True
            self.edit_bar.pack(side="bottom", fill="x", padx=(6, 0), pady=(0, 6), before=self.text_sb)
            self._fade(0)
        elif not n and self._bar_shown and self._hist_i == 0 and len(self._hist) == 1:
            self._bar_shown = False
            if self._fade_job:
                self.after_cancel(self._fade_job)
            self.edit_bar.pack_forget()
        elif self._bar_shown:
            self._paint_edit_bar(1.0)

    def _after_typing(self):
        """Typing paused (or the edit ended): the timeline and the player's captions catch up."""
        self._typing_job = None
        if hasattr(self, "_timeline_refresh"):
            self._timeline_refresh(now=True)
            self._refresh_preview_caps(now=True)

    def _fade(self, step: int, steps: int = 9):
        self._paint_edit_bar(step / steps)
        if step < steps:
            self._fade_job = self.after(16, self._fade, step + 1, steps)
        else:
            self._fade_job = None

    def _paint_edit_bar(self, t: float, hover=None):
        if not self._bar_shown:
            return
        c = self._edit_colors()
        bar_bg = _mix(c["frame"], c["bar"], t)
        self.edit_bar.configure(background=bar_bg, highlightbackground=_mix(c["frame"], c["border"], t),
                                highlightcolor=_mix(c["frame"], c["border"], t))
        can_undo, can_redo = self._hist_i > 0, self._hist_i < len(self._hist) - 1
        for b, enabled in ((self._bar_discard, bool(self._edits)), (self._bar_undo, can_undo),
                           (self._bar_redo, can_redo)):
            bg = c["hover"] if (hover is b and enabled) else c["bar"]
            fg = c["fg"] if enabled else _mix(c["bar"], c["muted"], 0.6)
            b.configure(background=_mix(c["frame"], bg, t), foreground=_mix(c["frame"], fg, t),
                        cursor="hand2" if enabled else "arrow")
        save_bg = c["acc_hover"] if hover is self._bar_save else c["acc"]
        enabled = bool(self._edits)
        if not enabled:
            save_bg = _mix(c["bar"], c["muted"], 0.35)
        self._bar_save.configure(background=_mix(c["frame"], save_bg, t),
                                 foreground=_mix(c["frame"], c["acc_fg"] if enabled else c["bar"], t),
                                 cursor="hand2" if enabled else "arrow")
        self._bar_count.configure(background=bar_bg, foreground=_mix(c["frame"], c["muted"], t))
