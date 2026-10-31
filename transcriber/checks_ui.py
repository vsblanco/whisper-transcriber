"""Check captions: a window listing what a viewer would trip over (captions that read too fast,
flicker, hang around, overlap, lines Whisper looped on), each a double-click away."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from transcriber.fonts import ui_font
from transcriber.icons import info_icon
from transcriber.ui_common import set_dark_titlebar


class ChecksMixin:

    def _check_captions(self):
        from transcriber.checks import check_all
        if not getattr(self, "display", None) or self.display[0] != "saved":
            self.status_var.set("Pick a transcript first.")
            return
        parts = getattr(self, "_pcaps_parts", None)
        cues = []
        if parts:
            try:
                self._preview_caps(parts)                  # the captions as shown now (pending edits too)
                cues = list(getattr(self, "_pcaps_cues", None) or [])
            except Exception:
                cues = []
        lines = []
        for meta in self._all_row_metas():
            if self._is_deleted(meta):
                continue
            text = self._edits.get(meta["key"], meta.get("orig")) or ""
            sec = float(self._shown_time(meta))
            lines.append((meta["key"], sec, sec, text))
        problems = check_all(cues, lines)
        win = getattr(self, "_checks_win", None)
        if win is None or not win.winfo_exists():
            win = self._checks_window()
        self._checks_fill(win, problems, len(cues))
        win.deiconify()
        win.lift()

    def _checks_window(self):
        win = tk.Toplevel(self)
        self._checks_win = win
        win.title("Check captions")
        win.transient(self)
        win.geometry("760x420")
        f = ttk.Frame(win, padding=(14, 12))
        f.pack(fill="both", expand=True)
        head = ttk.Frame(f)
        head.pack(fill="x", pady=(0, 8))
        ttk.Label(head, text="Check captions", font=ui_font(12, bold=True)).pack(side="left")
        info_icon(head, self, "Captions a viewer would trip over: reading too fast (over 20 characters a second), "
                              "on screen too briefly or too long, overlapping, lines too long, and transcript "
                              "lines where Whisper repeated itself. Double-click one to go to it. Unsaved edits "
                              "count, so fix and check again before saving.").pack(side="left", padx=6)
        win._summary = ttk.Label(head, text="", style="Muted.TLabel")
        win._summary.pack(side="left", padx=12)
        ttk.Button(head, text="Check again", command=self._check_captions).pack(side="right")
        cols = ("time", "problem", "detail", "text")
        tree = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for col, text, width in (("time", "Time", 80), ("problem", "Problem", 150), ("detail", "Why", 230), ("text", "Caption", 280)):
            tree.heading(col, text=text)
            tree.column(col, width=width, anchor="w", stretch=(col == "text"))
        sb = ttk.Scrollbar(f, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        p = self._palette()
        tree.tag_configure("bad", foreground="#e5484d")
        tree.tag_configure("warn", foreground=p["fg"])
        tree.bind("<Double-Button-1>", lambda e: self._checks_go(win))
        tree.bind("<Return>", lambda e: self._checks_go(win))
        win._tree = tree
        win._items = {}
        win.bind("<Escape>", lambda e: win.withdraw())
        set_dark_titlebar(win, self.current_theme == "dark")
        return win

    def _checks_fill(self, win, problems: list, n_cues: int):
        from transcriber.timeline import fmt
        tree = win._tree
        tree.delete(*tree.get_children())
        win._items = {}
        for p in problems:
            iid = tree.insert("", "end", values=(fmt(p["start"]), p["kind"], p["detail"], p["text"][:120]),
                              tags=(p["level"],))
            win._items[iid] = p
        bad = sum(1 for p in problems if p["level"] == "bad")
        if not problems:
            win._summary.configure(text=f"No problems in {n_cues} caption{'s' if n_cues != 1 else ''}.")
        else:
            win._summary.configure(text=f"{len(problems)} to look at ({bad} serious) in {n_cues} captions")
        self.status_var.set(win._summary.cget("text"))

    def _checks_go(self, win):
        """Double-click a problem: show that line in the transcript and jump the player there."""
        sel = win._tree.selection()
        p = win._items.get(sel[0]) if sel else None
        if not p:
            return
        key = p.get("src")
        if key is not None:
            key = tuple(key) if isinstance(key, list) else key
            line = self._tl_row(key) if hasattr(self, "_tl_row") else None
            if line is not None:
                self._set_hover(line)
            if getattr(self, "_tl", None) is not None and self.timeline_open:
                self._tl.select(key)
        try:
            self.player.seek(float(p["start"]), play=False)
        except Exception:
            pass
