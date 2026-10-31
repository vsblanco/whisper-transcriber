"""Ctrl+F: a floating find box in the top right of the transcript (or of the Library).

In the transcript, matches light up as you type and the rest of the text dims a little. The box
shows "3 of 12", with up/down arrows (or Enter / Shift+Enter) to step through. Esc closes it.

On the Library tab the same box searches every transcript: the list narrows to the ones that say it,
the arrows move through them, and Enter shows every matching line (in the Transcript tab). Opening a
transcript from the narrowed list keeps the search going in it. Esc clears it.
Used as a mixin by the App (needs self.text and self._palette()).
"""

from __future__ import annotations

import tkinter as tk

from transcriber.fonts import bind_keys, ui_font
from transcriber.icons import Tooltip, icon_family

UP, DOWN, CLOSE = "\ue74a", "\ue74b", "\ue711"
CHEV_RIGHT, CHEV_DOWN = "\ue76c", "\ue70d"
MATCH = {"light": ("#ffe58a", "#ff9e2c"), "dark": ("#6b5a12", "#c47a12")}


def _mix(a: str, b: str, t: float) -> str:
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


class FindMixin:

    def _find_setup(self):
        self._find_box = None
        self._find_matches: list[tuple[str, str]] = []
        self._find_i = -1
        self._find_scope = "transcript"            # or "library"
        self._find_queries = {"transcript": "", "library": ""}
        for k in ("f", "F"):
            bind_keys(self, k, lambda e: self._find_open())
        for k in ("h", "H"):
            bind_keys(self, k, lambda e: self._find_open(replace=True))

    def _find_colors(self):
        mode = getattr(self, "current_theme", None) or "light"
        p = self._palette()
        return mode, p

    def _find_theme(self):
        """Tag colors for the current theme (call after a theme change)."""
        mode, p = self._find_colors()
        t = self.text
        t.tag_configure("find_dim", foreground=_mix(p["fg"], p["bg"], 0.55))
        t.tag_configure("find", background=MATCH[mode][0], foreground=p["fg"])
        t.tag_configure("find_cur", background=MATCH[mode][1], foreground="#000000" if mode == "dark" else "#1c1c1c")
        for tag in ("find_dim", "find", "find_cur"):
            t.tag_raise(tag)
        t.tag_raise("sel")
        if self._find_box is not None:
            self._find_style_box()

    # ------------------------------------------------------------ the box

    def _find_open(self, replace: bool = False):
        scope = "library" if getattr(self, "_tab_name", lambda: "")() == "library" and not replace else "transcript"
        if scope == "transcript":
            if hasattr(self, "_show_tab"):
                self._show_tab("transcript")
            if getattr(self, "_editing", None) is not None:
                self._end_edit()
        if self._find_box is None:
            self._find_build()
        self._find_set_scope(scope)
        if replace and not self._find_replacing:
            self._find_toggle_replace()
        box = self._find_box
        m = self._find_margin
        if scope == "transcript":
            box.place(in_=self.text, relx=1.0, x=-14 + m, y=10 - m, anchor="ne")
        else:                                       # on the tab row, right of the tabs
            box.place(in_=self.tabs, relx=1.0, x=-2 + m, y=-m, anchor="ne")
        tk.Misc.lift(box)          # (a Canvas has its own lift for drawn items)
        self._find_entry.focus_set()
        self._find_entry.select_range(0, "end")
        self._find_run(keep_position=True)
        return "break"

    def _find_set_scope(self, scope: str):
        """Searching the transcript or the Library: each keeps its own text."""
        if self._find_box is None:
            return
        if scope != self._find_scope:
            self._find_queries[self._find_scope] = self._find_var.get()
            self._find_scope = scope
            self._find_var.set(self._find_queries[scope])
        library = scope == "library"
        if library and self._find_replacing:
            self._find_toggle_replace()
        self._find_hint.configure(text="Search all transcripts" if library else "Find in transcript")
        if library:
            self._find_chev.pack_forget()
        elif not self._find_chev.winfo_ismapped():
            self._find_chev.pack(side="left", fill="y", padx=(0, 4), before=self._find_entry)
        self._find_style_box()

    def _find_build(self):
        """A rounded box with a soft shadow (drawn as an image), with the controls on top:
        [> Find in transcript      2/9 | ↑  ↓  ✕]   and, opened with > or Ctrl+H,
        [  Replace with              Replace  All ]"""
        fam = icon_family(self)
        sc = getattr(self, "ui_scale", 1.0)
        self._find_sc = sc
        self._find_w, self._find_row_h = int(330 * sc), int(40 * sc)
        self._find_h = self._find_row_h
        self._find_replacing = False
        self._find_margin = m = int(12 * sc)
        # (in the tab row's frame, so it can float over the transcript or over the Library list)
        holder = getattr(self, "tabs", None) or self.text.master
        box = tk.Canvas(holder, width=self._find_w + 2 * m, height=self._find_h + 2 * m,
                        highlightthickness=0, borderwidth=0)
        self._find_box = box
        self._find_bg_item = box.create_image(0, 0, anchor="nw")
        inner = tk.Frame(box, borderwidth=0)
        self._find_inner = inner
        self._find_win = box.create_window(m + int(4 * sc), m + int(5 * sc), window=inner, anchor="nw",
                                           width=self._find_w - int(10 * sc), height=self._find_h - int(10 * sc))
        row1 = tk.Frame(inner, borderwidth=0)
        row1.pack(fill="x", expand=True)
        self._find_rows = [inner, row1]

        def icon(parent, glyph, fallback, tip, command, size=10, width=2):
            use_glyph = fam and glyph
            b = tk.Label(parent, text=glyph if use_glyph else fallback,
                         font=(fam, size) if use_glyph else ui_font(9 if not glyph else size),
                         width=width, cursor="hand2", padx=4 if not glyph else 0)
            b.bind("<Button-1>", lambda e: command())
            b.bind("<Enter>", lambda e: b.configure(background=self._find_hover))
            b.bind("<Leave>", lambda e: b.configure(background=self._find_bg))
            Tooltip(b, tip)
            return b

        self._find_chev = icon(row1, CHEV_RIGHT, "›", "Replace (Ctrl+H)", self._find_toggle_replace, size=8)
        self._find_chev.pack(side="left", fill="y", padx=(0, 4))
        self._find_var = tk.StringVar()
        entry = tk.Entry(row1, textvariable=self._find_var, relief="flat", borderwidth=0,
                         highlightthickness=0, font=ui_font(10))
        entry.pack(side="left", fill="x", expand=True)
        self._find_entry = entry
        self._find_hint = tk.Label(row1, text="Find in transcript", font=ui_font(10), cursor="xterm")
        self._find_hint.place(in_=entry, x=0, rely=0.5, anchor="w")
        self._find_hint.bind("<Button-1>", lambda e: entry.focus_set())

        self._find_close_btn = icon(row1, CLOSE, "✕", "Close (Esc)", self._find_close)
        self._find_close_btn.pack(side="right", fill="y")
        self._find_down = icon(row1, DOWN, "↓", "Next (Enter)", lambda: self._find_step(1))
        self._find_down.pack(side="right", fill="y", padx=(2, 2))
        self._find_up = icon(row1, UP, "↑", "Previous (Shift+Enter)", lambda: self._find_step(-1))
        self._find_up.pack(side="right", fill="y")
        self._find_divider = tk.Frame(row1, width=1)
        self._find_divider.pack(side="right", fill="y", pady=int(5 * sc), padx=(8, 6))
        self._find_count = tk.Label(row1, font=ui_font(9))
        self._find_count.pack(side="right")

        # Replace row (hidden until opened)
        row2 = tk.Frame(inner, borderwidth=0)
        self._find_row2 = row2
        spacer = tk.Frame(row2, width=int(24 * sc))
        spacer.pack(side="left")
        self._find_rows += [row2, spacer]
        self._rep_var = tk.StringVar()
        rep = tk.Entry(row2, textvariable=self._rep_var, relief="flat", borderwidth=0,
                       highlightthickness=0, font=ui_font(10))
        rep.pack(side="left", fill="x", expand=True)
        self._rep_entry = rep
        self._rep_hint = tk.Label(row2, text="Replace with", font=ui_font(10), cursor="xterm")
        self._rep_hint.place(in_=rep, x=0, rely=0.5, anchor="w")
        self._rep_hint.bind("<Button-1>", lambda e: rep.focus_set())
        self._rep_var.trace_add("write", lambda *_: self._find_update_buttons())
        self._rep_all = icon(row2, None, "All", "Replace all (Ctrl+Enter)", self._find_replace_all, width=0)
        self._rep_all.pack(side="right", fill="y")
        self._rep_one = icon(row2, None, "Replace", "Replace (Enter)", self._find_replace_one, width=0)
        self._rep_one.pack(side="right", fill="y", padx=(0, 2))
        self._find_btns = [self._find_up, self._find_down, self._find_close_btn, self._find_chev,
                           self._rep_one, self._rep_all]

        self._find_var.trace_add("write", lambda *_: self._find_run())
        for e in (entry, rep):
            e.bind("<Escape>", lambda ev: self._find_close() or "break")
            e.bind("<Down>", lambda ev: self._find_step(1) or "break")
            e.bind("<Up>", lambda ev: self._find_step(-1) or "break")
        entry.bind("<Return>", lambda e: (self._find_library_results() if self._find_scope == "library"
                                          else self._find_step(1)) or "break")
        entry.bind("<Shift-Return>", lambda e: self._find_step(-1) or "break")
        rep.bind("<Return>", lambda e: self._find_replace_one() or "break")
        rep.bind("<Control-Return>", lambda e: self._find_replace_all() or "break")
        self._find_style_box()

    def _find_toggle_replace(self):
        fam = icon_family(self)
        self._find_replacing = not self._find_replacing
        sc = self._find_sc
        if self._find_replacing:
            self._find_row2.pack(fill="x", expand=True, pady=(int(2 * sc), 0))
            self._find_h = self._find_row_h * 2 - int(6 * sc)
            self._find_chev.configure(text=CHEV_DOWN if fam else "⌄")
        else:
            self._find_row2.pack_forget()
            self._find_h = self._find_row_h
            self._find_chev.configure(text=CHEV_RIGHT if fam else "›")
        m = self._find_margin
        self._find_box.configure(height=self._find_h + 2 * m)
        self._find_box.itemconfigure(self._find_win, height=self._find_h - int(10 * sc))
        self._find_style_box()
        (self._rep_entry if self._find_replacing else self._find_entry).focus_set()

    # ---- replacing: changes go into the same pending edits as typing (yellow, Save / Undo bar)

    def _find_line_of(self, a: str, b: str):
        """The editable line a match is in, or None (names, times and track labels aren't replaced)."""
        t = self.text
        line = int(a.split(".")[0])
        meta = getattr(self, "_row_meta", {}).get(line)
        if not meta or (hasattr(self, "_is_deleted") and self._is_deleted(meta)):
            return None
        if t.compare(a, ">=", f"b{line}s") and t.compare(b, "<=", f"b{line}e"):
            return line
        return None

    def _find_span(self, line, a, b):
        t = self.text
        return len(t.get(f"b{line}s", a)), len(t.get(f"b{line}s", b))

    def _find_replace_lines(self, targets: dict) -> int:
        """targets = {line: [(start offset, end offset), ...]} inside each line's text."""
        new = dict(self._edits)
        n = 0
        rep = self._rep_var.get()
        t = self.text
        for line, spans in targets.items():
            meta = self._row_meta[line]
            text = t.get(f"b{line}s", f"b{line}e")
            for a, b in sorted(spans, reverse=True):
                text = text[:a] + rep + text[b:]
                n += 1
            if " ".join(text.split()) != " ".join(meta["orig"].split()):
                new[meta["key"]] = text
            else:
                new.pop(meta["key"], None)
        if new != self._edits:
            self._push_state(new)
            self._apply_edit_state(new)
        return n

    def _find_replace_one(self):
        if not self._find_matches or self._find_i < 0:
            return
        a, b = self._find_matches[self._find_i]
        line = self._find_line_of(a, b)
        if line is None:
            self.bell()
            self._find_step(1)
            return
        i = self._find_i
        self._find_replace_lines({line: [self._find_span(line, a, b)]})
        self._find_run()
        if self._find_matches:
            self._find_i = min(i, len(self._find_matches) - 1)
            self._find_show()

    def _find_replace_all(self):
        targets = {}
        for a, b in self._find_matches:
            line = self._find_line_of(a, b)
            if line is not None:
                targets.setdefault(line, []).append(self._find_span(line, a, b))
        if not targets:
            self.bell()
            return
        n = self._find_replace_lines(targets)
        self._find_run()
        if hasattr(self, "status_var"):
            self.status_var.set(f"Replaced {n} match{'es' if n != 1 else ''}. Save to keep the changes.")

    def _find_style_box(self):
        from PIL import Image, ImageDraw, ImageFilter, ImageTk
        mode, p = self._find_colors()
        dark = mode == "dark"
        self._find_bg = "#2a2a2a" if dark else "#ffffff"
        self._find_hover = "#3a3a3a" if dark else "#eeeeee"
        border = "#3d3d3d" if dark else "#dadada"
        icon_fg = "#c8c8c8" if dark else "#5a5a5a"

        # Background: the color under it, a blurred shadow, then the rounded box with a thin edge.
        w, h, m = self._find_w, self._find_h, self._find_margin
        W, H, k = w + 2 * m, h + 2 * m, 2                     # drawn at 2x, then scaled down (smooth corners)
        under = p["bg"]
        if self._find_scope == "library":
            from tkinter import ttk
            under = str(ttk.Style(self).lookup("TNotebook", "background") or "") or \
                str(ttk.Style(self).lookup("TFrame", "background") or "") or p["bg"]
            if not under.startswith("#"):
                try:
                    r, g, b = (v // 256 for v in self.winfo_rgb(under))
                    under = f"#{r:02x}{g:02x}{b:02x}"
                except Exception:
                    under = p["bg"]
        img = Image.new("RGBA", (W * k, H * k), under)
        shadow = Image.new("RGBA", (W * k, H * k), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            (m * k, (m + 2) * k, (m + w) * k, (m + h + 2) * k), radius=8 * k,
            fill=(0, 0, 0, 150 if dark else 55))
        shadow = shadow.filter(ImageFilter.GaussianBlur(5 * k))
        img.alpha_composite(shadow)
        ImageDraw.Draw(img).rounded_rectangle((m * k, m * k, (m + w) * k - 1, (m + h) * k - 1), radius=8 * k,
                                              fill=self._find_bg, outline=border, width=k)
        self._find_img = ImageTk.PhotoImage(img.resize((W, H), Image.LANCZOS))
        self._find_box.configure(background=under)
        self._find_box.itemconfigure(self._find_bg_item, image=self._find_img)

        for f in self._find_rows:
            f.configure(background=self._find_bg)
        self._rep_entry.configure(background=self._find_bg, foreground=p["fg"], insertbackground=p["fg"],
                                  selectbackground=p["select"], selectforeground=p["fg"])
        self._rep_hint.configure(background=self._find_bg, foreground="#8a8a8a")
        self._find_entry.configure(background=self._find_bg, foreground=p["fg"], insertbackground=p["fg"],
                                   selectbackground=p["select"], selectforeground=p["fg"])
        self._find_hint.configure(background=self._find_bg, foreground="#8a8a8a")
        self._find_count.configure(background=self._find_bg, foreground=p["muted"])
        self._find_divider.configure(background=border)
        for b in self._find_btns:
            b.configure(background=self._find_bg, foreground=icon_fg)
        self._find_close_btn.configure(foreground=p["fg"])
        self._find_update_buttons()

    def _find_update_buttons(self):
        """Arrows dim when there's nothing to step through; the hint shows while the box is empty."""
        if self._find_box is None:
            return
        for var, hint, entry in ((self._find_var, self._find_hint, self._find_entry),
                                 (self._rep_var, self._rep_hint, self._rep_entry)):
            if var.get():
                hint.place_forget()
            else:
                hint.place(in_=entry, x=0, rely=0.5, anchor="w")
        mode, p = self._find_colors()
        on = "#c8c8c8" if mode == "dark" else "#5a5a5a"
        off = "#5c5c5c" if mode == "dark" else "#b8b8b8"
        for b in (self._find_up, self._find_down, self._rep_one, self._rep_all):
            b.configure(foreground=on if self._find_matches else off,
                        cursor="hand2" if self._find_matches else "arrow")

    def _find_close(self):
        if self._find_scope == "library":           # Esc in the Library: the whole list again
            if self._find_box is not None:
                self._find_box.place_forget()
                self._find_var.set("")
            self._find_queries["library"] = ""
            self.search_var.set("")
            self.saved.focus_set()
            return
        if self._find_box is not None:
            self._find_box.place_forget()
        for tag in ("find_dim", "find", "find_cur"):
            self.text.tag_remove(tag, "1.0", "end")
        self._find_matches, self._find_i = [], -1
        self.text.focus_set()

    def _find_is_open(self) -> bool:
        return self._find_box is not None and self._find_box.winfo_ismapped()

    # ------------------------------------------------------------ searching

    def _find_run(self, keep_position: bool = False):
        if self._find_scope == "library":
            query = self._find_var.get() if self._find_box is not None else ""
            self._find_queries["library"] = query
            if self.search_var.get() != query:
                self.search_var.set(query)              # (the list narrows a moment later)
            self._find_update_buttons()
            return
        t = self.text
        for tag in ("find_dim", "find", "find_cur"):
            t.tag_remove(tag, "1.0", "end")
        query = self._find_var.get() if self._find_box is not None else ""
        old = self._find_matches[self._find_i][0] if (keep_position and 0 <= self._find_i < len(self._find_matches)) else None
        self._find_matches, self._find_i = [], -1
        if not query.strip():
            self._find_count.configure(text="")
            self._find_update_buttons()
            return
        count = tk.IntVar()
        start = "1.0"
        while True:
            pos = t.search(query, start, stopindex="end", nocase=True, count=count)
            if not pos or count.get() == 0:
                break
            end = f"{pos} + {count.get()} chars"
            self._find_matches.append((pos, t.index(end)))
            start = end
        if not self._find_matches:
            self._find_count.configure(text="0/0")
            self._find_update_buttons()
            return
        t.tag_add("find_dim", "1.0", "end")
        for tag in ("find_dim", "find", "find_cur"):   # above name colors made since the last search
            t.tag_raise(tag)
        t.tag_raise("sel")
        for a, b in self._find_matches:
            t.tag_add("find", a, b)
        # Start at the first match at or below the top of the view (or where we were).
        top = old or t.index("@0,0")
        self._find_i = next((i for i, (a, _) in enumerate(self._find_matches) if t.compare(a, ">=", top)), 0)
        self._find_show()

    def _find_step(self, d: int):
        if self._find_scope == "library":           # move through the narrowed list
            kids = self.saved.get_children()
            if not kids:
                return
            sel = self.saved.selection()
            i = kids.index(sel[0]) + d if sel and sel[0] in kids else (0 if d > 0 else len(kids) - 1)
            iid = kids[max(0, min(i, len(kids) - 1))]
            self.saved.selection_set(iid)
            self.saved.see(iid)
            return
        if not self._find_matches:
            return
        self._find_i = (self._find_i + d) % len(self._find_matches)
        self._find_show()

    def _find_library_results(self):
        """Enter in the Library search: every matching line, in the Transcript tab."""
        query = self._find_var.get().strip()
        if not query:
            return
        self._list_mode = "library"
        self._show_tab("transcript")

        def show():                    # (after the list's own selection has settled)
            shown = [self.saved_items[i] for i in self.saved.get_children() if i in self.saved_items]
            self._show_search_results(query.lower(), shown)
            self._find_after_render()
        self.after(80, show)

    def _find_library_count(self):
        """'3 found' while the Library search narrows the list."""
        if self._find_box is None or self._find_scope != "library":
            return
        n = len(self.saved.get_children()) if self._find_var.get().strip() else 0
        self._find_count.configure(text=f"{n} found" if self._find_var.get().strip() else "")

    def _find_tab_changed(self, name: str):
        """Keep the box with its tab: the Library search shows on Library; opening a transcript from
        a narrowed Library keeps looking for the same words in it."""
        if self._find_box is None:
            return
        lib = self._find_queries["library"] if self._find_scope != "library" else self._find_var.get()
        if name == "library" and lib.strip():
            self._find_open()
        elif name == "transcript" and self._find_scope == "library" and lib.strip():
            self._find_queries["transcript"] = lib
            self.after_idle(self._find_open)
        elif self._find_box.winfo_ismapped() and (name == "queue" or (name == "library") != (self._find_scope == "library")):
            if self._find_scope == "transcript":
                self._find_close()
            else:
                self._find_box.place_forget()

    def _find_show(self):
        t = self.text
        t.tag_remove("find_cur", "1.0", "end")
        a, b = self._find_matches[self._find_i]
        t.tag_add("find_cur", a, b)
        t.see(a)
        self._find_count.configure(text=f"{self._find_i + 1}/{len(self._find_matches)}")
        self._find_update_buttons()

    def _find_after_render(self):
        """The transcript was redrawn (another file, an edit saved...): search it again."""
        if self._find_is_open():
            self._find_run()
