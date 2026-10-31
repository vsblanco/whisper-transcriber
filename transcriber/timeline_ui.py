"""The subtitle timeline at the bottom of the window (see timeline.py for the widget itself).

Lanes come from the transcript shown: one per track (or one for the recording), each with its
lines as blocks and its audio's waveform. Moving or resizing a block is an edit like any other:
it shows in the transcript, the edit bar offers Save / Discard, and Undo takes it back. The
player's captions follow the change right away.

A mixin for the App class in app.py."""

from __future__ import annotations

import bisect
import os
import threading
import tkinter as tk
from tkinter import ttk

from transcriber.icons import info_icon
from transcriber.timeline import Timeline, command_held

LANE_COLORS = ["#3b82f6", "#f97316", "#10b981", "#a855f7"]


class TimelineMixin:

    def _timeline_setup(self, holder, header):
        """holder: where the panel goes (above the status bar); header: where its button goes."""
        self._tl_holder = holder
        self._tl = None
        self._tl_orig: dict = {}           # block key -> (start, end) as saved
        self._tl_seg_cache: dict = {}
        self._tl_env_cache: dict = {}
        self._tl_env_pending: set = set()
        self._tl_job = None
        self.timeline_var = tk.BooleanVar(value=self.timeline_open)
        btn = ttk.Checkbutton(header, text="Timeline", variable=self.timeline_var, command=self._timeline_toggle,
                              style="Toggle.TButton" if self._has_sv() else "TCheckbutton")
        btn.pack(side="right", padx=(0, 6))
        from transcriber.icons import Tooltip
        Tooltip(btn, "Show the subtitle timeline: drag lines to change when they start and end")
        self.bind("<KeyPress>", self._tl_tool_key, add="+")      # the tools' keys (Settings > Timeline)
        if self.timeline_open:
            self.after_idle(self._timeline_build)

    @staticmethod
    def _has_sv() -> bool:
        from transcriber.optional import HAS_SV
        return HAS_SV

    def _timeline_toggle(self):
        self.timeline_open = bool(self.timeline_var.get())
        if self.timeline_open:
            self._timeline_build()
        elif self._tl is not None:
            for w in self._tl_holder.winfo_children():
                w.pack_forget()
            # An emptied frame keeps its old height, so take the whole panel out: the rest of the
            # window grows back into the space.
            self._tl_holder.pack_forget()
        self.after_idle(self._fit_preview)              # the picture gives or takes the room
        from transcriber.ui_common import save_settings
        save_settings(self._settings())

    def _timeline_build(self):
        holder = self._tl_holder
        if not holder.winfo_manager():                  # (taken out when the timeline was turned off)
            holder.pack(side="bottom", fill="x", before=self._preview_box)
        from transcriber.icons import Tooltip
        if self._tl is None:
            self._tl_sash = tk.Frame(holder, height=max(int(6 * self.ui_scale), 5), cursor="sb_v_double_arrow",
                                     highlightthickness=0)
            self._tl_sash.bind("<ButtonPress-1>", self._tl_sash_press)
            self._tl_sash.bind("<B1-Motion>", self._tl_sash_move)
            self._tl_sash.bind("<ButtonRelease-1>", lambda e: self._tl_sash_release())
            self._tl = Timeline(holder, palette=self._palette, dark=lambda: self.current_theme == "dark",
                                scale=self.ui_scale, on_change=self._tl_change, on_seek=self._tl_seek,
                                on_select=self._tl_select, on_edit_text=self._tl_edit,
                                on_snip=self._tl_snip, snip_at=self._tl_snip_at, on_delete=self._tl_delete,
                                on_menu=self._tl_menu, on_copy=self._tl_copy, on_paste=self._tl_paste,
                                on_layer=self._tl_layer, on_range=self._tl_range,
                                on_play_range=self._tl_play_range, on_export_range=self._tl_export_range,
                                keys=lambda: self.timeline_keys,
                                tooltip=Tooltip, info_icon=lambda parent, tip: info_icon(parent, self, tip))
            self._tl.canvas.configure(height=self.timeline_h)
        self._tl_sash.configure(background=self._palette()["hover"])
        self._tl_sash.pack(fill="x")
        self._tl.pack(fill="both", expand=True, pady=(0, 2))
        self._timeline_refresh(now=True)
        self.after_idle(self._fit_preview)

    # ---------------------------------------------------------------- tools

    def _tl_tool_key(self, e):
        """S / B (or the keys picked in Settings): the timeline's Select and Snip tools. Not while
        typing, and not with Ctrl or Alt held (Ctrl+S and friends stay what they are)."""
        if self._tl is None or not self.timeline_open or not e.char or command_held(e):
            return None
        if self._editing is not None or getattr(self, "_time_entry", None):
            return None
        w = e.widget
        if isinstance(w, (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Combobox)) or (isinstance(w, tk.Text) and w is not self.text):
            return None
        k = e.char.lower()
        for tool, key in self.timeline_keys.items():
            if k == key:
                self._tl.set_tool(tool)
                self.status_var.set({"snip": "Snip tool: click a line on the timeline to split it",
                                     "range": "Range tool: drag over the part of the timeline to play or export"
                                     }.get(tool, "Select tool"))
                return "break"
        return None

    # ---------------------------------------------------------------- a marked stretch

    def _tl_range(self, start, end):
        if start is None:
            self.status_var.set("")
            return
        self.status_var.set(f"{_fmt(start)} \u2013 {_fmt(end)} marked ({end - start:.1f} s). "
                            "\u25b6 plays just that part, Clip exports it, Esc clears it.")

    def _tl_play_range(self, start, end):
        """Play just the marked stretch in the player (captions as they'd be exported), then pause."""
        self.player.play_range(start, end)

    def _tl_export_range(self, start, end):
        """Export the marked stretch as a clip (the Export clip window, with these times filled in)."""
        self._clip_dialog(None, start=start, end=end)

    def _tl_snip_at(self, key, t):
        """Where the snip tool would split a line near time t: (time between two words, left
        text, right text), or None. Uses when each word was said when the text still matches."""
        meta = self._tl_meta(key)
        found = self._tl._block(key) if self._tl else None
        if meta is None or not found or self._is_deleted(meta):
            return None
        b = found[1]
        text = self._edits.get(meta["key"]) or meta["orig"] or ""
        words = text.split()
        if len(words) < 2:
            return None
        s, e = float(b["start"]), float(b["end"])
        times = None
        wp = meta["files"].get("words")
        lines = self._words_for(wp) if wp else None
        if lines and meta["i"] < len(lines) and lines[meta["i"]]:
            ws = lines[meta["i"]]
            if " ".join(w[2].strip() for w in ws) == " ".join(words):
                off = s - self._tl_orig.get(key, (s, e))[0]          # (moved on the timeline, not saved yet)
                times = [float(ws[n][0]) + off for n in range(1, len(ws))]
        if times is None:                       # no word times: split in proportion to the text
            total = max(len(text), 1)
            pos, times = 0, []
            for wd in words[:-1]:
                pos += len(wd) + 1
                times.append(s + (e - s) * pos / total)
        n = min(range(len(times)), key=lambda j: abs(times[j] - t)) + 1
        at = min(max(times[n - 1], s + 0.05), e - 0.05)
        return at, " ".join(words[:n]), " ".join(words[n:])

    def _tl_snip(self, key, t):
        """The snip tool clicked a line: split it there (saved right away; Ctrl+Z undoes it)."""
        res = self._tl_snip_at(key, t)
        meta = self._tl_meta(key)
        found = self._tl._block(key) if self._tl else None
        if not res or meta is None or not found:
            return
        at, left, right = res
        b = found[1]
        self._split_meta(meta, left, right, start=float(b["start"]), new_start=at, end=float(b["end"]))
        self.status_var.set(f"Split at {_fmt(at)}. Ctrl+Z to undo.")

    # ---------------------------------------------------------------- resizing the panel

    def _tl_sash_press(self, e):
        self._tl_drag = (e.y_root, int(self._tl.canvas.cget("height")))

    def _tl_sash_move(self, e):
        y0, h0 = getattr(self, "_tl_drag", (e.y_root, self.timeline_h))
        h = min(max(h0 + (y0 - e.y_root), int(70 * self.ui_scale)), int(self.winfo_height() * 0.6))
        self._tl.canvas.configure(height=h)
        self._fit_preview()

    def _tl_sash_release(self):
        self.timeline_h = int(self._tl.canvas.cget("height"))
        from transcriber.ui_common import save_settings
        save_settings(self._settings())

    # ---------------------------------------------------------------- lanes from the transcript

    def _timeline_refresh(self, now: bool = False):
        """Show the shown transcript's lines (soon: several changes in a row redraw once)."""
        if not getattr(self, "timeline_open", False) or self._tl is None:
            return
        if not now:
            if self._tl_job is None:
                self._tl_job = self.after(60, lambda: self._timeline_refresh(now=True))
            return
        if self._tl_job is not None:
            try:
                self.after_cancel(self._tl_job)
            except tk.TclError:
                pass
            self._tl_job = None
        try:
            self._timeline_fill()
        except Exception as exc:                  # never let the timeline break the window
            self._tl.set_lanes([], 60.0, doc=("error", str(exc)))

    def _tl_segments(self, files):
        """(segments, speakers data) of a version, cached until its file changes."""
        from transcriber.speakers import read_speakers_file
        sp = files.get("speakers")
        if not sp:
            return [], {}
        try:
            key = (str(sp), os.path.getmtime(sp))
        except OSError:
            return [], {}
        hit = self._tl_seg_cache.get(key)
        if hit is None:
            data = read_speakers_file(sp) or {}
            hit = self._tl_seg_cache[key] = (data.get("segments", []), data)
            if len(self._tl_seg_cache) > 40:
                self._tl_seg_cache.clear()
                self._tl_seg_cache[key] = hit
        return hit

    def _timeline_fill(self):
        from transcriber.speakers import display_names
        tl = self._tl
        item = self._shown_item() if self.display and self.display[0] == "saved" else None
        metas = self._all_row_metas() if item else []
        if not metas:
            tl.set_lanes([], 60.0, doc=None)
            return
        source = item.get("source")
        session = {}
        if item.get("session"):
            from transcriber.session import read_session
            session = {f["label"]: (f["path"], float(f.get("offset", 0)))
                       for f in (read_session(item["session"]) or {}).get("files") or []}
        lanes, order = {}, []
        self._tl_orig = {}
        unnamed: dict = {}                 # speakers with no person yet still get a color each
        names_of: dict = {}                # per file: speaker label -> shown name (worked out once)
        spans = self._tl_caption_spans()
        seg_of: dict = {}                  # per file (thousands of rows share a few files)
        for meta in sorted(metas, key=lambda m: float(m.get("sec", 0))):
            if self._is_deleted(meta):
                continue
            fk = meta["fkey"]
            if fk not in seg_of:
                seg_of[fk] = self._tl_segments(meta["files"])
            segs, data = seg_of[fk]
            i = meta["i"]
            seg = segs[i] if 0 <= i < len(segs) else {}
            s0 = float(seg.get("start", meta.get("sec", 0.0)))
            e0 = float(seg.get("end", s0 + 2.0))
            self._tl_orig[meta["key"]] = (s0, e0)
            t = self._edits.get(self._time_key(meta))
            if isinstance(t, (tuple, list)):
                s, e = float(t[0]), float(t[1])
            elif t is not None:
                s, e = float(t), float(t) + (e0 - s0)
            else:
                s, e = s0, e0
            text = self._edits.get(meta["key"], meta["orig"]) or meta["orig"]
            label = seg.get("speaker")
            name, color = "", ""
            if label:
                fk = meta["fkey"]
                if fk not in names_of:
                    names_of[fk] = display_names(data, self.people) if data else {}
                name = names_of[fk].get(label, "")
                person = self.people.get(((data.get("speakers") or {}).get(label) or {}).get("person"))
                if person:
                    color = person["color"]
                else:
                    color = unnamed.setdefault(name or label, LANE_COLORS[len(unnamed) % len(LANE_COLORS)])
            track = meta.get("track") or ""
            if track not in lanes:
                lanes[track] = []
                order.append(track)
            lk = self._layer_key(meta)
            layer = self._edits[lk] if lk in self._edits else seg.get("layer")
            shown = spans.get(meta["key"])              # when its caption is on screen (can outlast the line)
            lanes[track].append({"key": meta["key"], "start": s, "end": e, "text": text, "name": name,
                                 "color": color, "layer": int(layer or 0),
                                 "shown": shown if shown and (shown[1] > e + 0.05 or shown[0] < s - 0.05) else None,
                                 "edited": meta["key"] in self._edits or self._time_key(meta) in self._edits
                                 or lk in self._edits})
        out = []
        for n, track in enumerate(order):
            fallback = LANE_COLORS[n % len(LANE_COLORS)]
            for b in lanes[track]:
                b["color"] = b["color"] or fallback
            out.append({"name": track or "Audio", "blocks": lanes[track]})
        duration = (self.player.length if getattr(self.player, "path", None) else 0.0) or 0.0
        # Which transcript and version this is, by its files, not their save time: saving an edit keeps
        # where you've zoomed and scrolled to.
        v = self._current_version(item) or {}

        def ident(files):
            files = files or {}
            return str(files.get("speakers") or files.get("txt") or "")
        doc = (item.get("key"), self._track_key(item),
               ident(v.get("files")) or "|".join(sorted(ident(pv.get("files")) for pv in (v.get("parts") or {}).values())))
        tl.set_lanes(out, duration, doc=doc)
        # the waveforms: per track (its own mic, or its audio track in the file), loaded once
        for track in order:
            path, off = session.get(track, (source, 0.0))
            stream = self._source_track_index(source, track) if (track and track not in session) else None
            self._tl_wave(track or "Audio", path, stream, off)

    def _tl_wave(self, lane, path, stream, offset):
        if not path or not os.path.exists(path):
            return
        try:
            key = (path, os.path.getmtime(path), stream)
        except OSError:
            return
        env = self._tl_env_cache.get(key)
        if env is not None:
            self._tl.set_envelope(lane, env, offset)
            return
        if key in self._tl_env_pending:
            return
        self._tl_env_pending.add(key)

        def work():
            from transcriber.session import envelope
            try:
                env = envelope(path, stream=stream)
            except Exception:
                env = None
            self._tl_env_pending.discard(key)
            if env is not None and len(env):
                self._tl_env_cache[key] = env
                if len(self._tl_env_cache) > 8:
                    self._tl_env_cache.pop(next(iter(self._tl_env_cache)))
                self.events.put(("call", lambda: self._tl and self._tl.set_envelope(lane, env, offset)))
        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- changes from the timeline

    def _tl_meta(self, key):
        return next((m for m in self._all_row_metas() if m.get("key") == key), None)

    def _tl_change(self, key, start, end):
        meta = self._tl_meta(key)
        if meta is None:
            return
        s0, e0 = self._tl_orig.get(key, (start, end))
        new = dict(self._edits)
        tk_ = self._time_key(meta)
        if abs(start - s0) < 0.005 and abs(end - e0) < 0.005:
            new.pop(tk_, None)
        else:
            new[tk_] = (round(start, 3), round(end, 3))
        if new != self._edits:
            self._push_state(new)
            self._apply_edit_state(new)
            self.status_var.set(f"{_fmt(start)} – {_fmt(end)}. Save to keep it (or Undo).")

    def _tl_delete(self, key):
        """The Delete key on a line in the timeline: mark it deleted, like Delete line in the transcript
        (saved with Save; Ctrl+Z brings it back)."""
        meta = self._tl_meta(key)
        if meta is None or self._is_deleted(meta):
            return
        if self._editing is not None:
            self._end_edit()
        new = dict(self._edits)
        new[meta["key"]] = None
        self._push_state(new)
        self._apply_edit_state(new)
        if meta not in self._row_meta.values():      # (inside a folded paragraph: draw it again)
            self._rerender_in_place()
        self.status_var.set("Line deleted. Save to keep it, or Ctrl+Z to bring it back.")

    def _tl_copy(self, key):
        """Ctrl+C on the timeline: remember the line (its text also goes to the clipboard)."""
        meta = self._tl_meta(key)
        found = self._tl._block(key) if self._tl else None
        if meta is None or not found:
            return
        b = found[1]
        segs, _data = self._tl_segments(meta["files"])
        seg = segs[meta["i"]] if meta["i"] < len(segs) else {}
        text = self._edits.get(meta["key"]) or meta["orig"]
        self._tl_clip = {"files": meta["files"], "fkey": meta["fkey"], "text": text, "speaker": seg.get("speaker"),
                         "length": max(float(b["end"]) - float(b["start"]), 0.3)}
        self.clipboard_clear()
        self.clipboard_append(text)
        short = text if len(text) <= 50 else text[:50] + "\u2026"
        self.status_var.set(f"Copied \u201c{short}\u201d. Move the playhead and press Ctrl+V to paste it there.")

    def _tl_paste(self):
        """Ctrl+V on the timeline: a copy of the copied line at the playhead, on its track (moved past
        a line already there, and shortened to fit before the next one)."""
        clip = getattr(self, "_tl_clip", None)
        if not clip or self._tl is None:
            self.status_var.set("Copy a line on the timeline first (click it, Ctrl+C).")
            return
        lane = next((ln for ln in self._tl.lanes for b in ln["blocks"]
                     if (self._tl_meta(b["key"]) or {}).get("fkey") == clip["fkey"]), None)
        blocks = sorted(lane["blocks"], key=lambda b: b["start"]) if lane else []
        t = self._tl.time if self._tl.time is not None else self._tl.t0
        for b in blocks:
            if b["start"] <= t < b["end"]:
                t = b["end"] + 0.05                       # (on a line: right after it)
        nxt = next((b["start"] for b in blocks if b["start"] >= t), None)
        end = t + clip["length"]
        if nxt is not None:
            end = min(end, nxt - 0.05)
        if end - t < 0.3:
            self.bell()
            self.status_var.set("No room at the playhead for the line. Move the playhead to a gap.")
            return
        if self._insert_line(clip["files"], clip["fkey"], t, end, clip["text"], clip["speaker"]):
            self.status_var.set(f"Pasted at {_fmt(t)}. Ctrl+Z to undo.")

    def _tl_layer(self, key, layer):
        """A line dragged to another caption spot on the timeline: stacked above (1, 2) or back to
        the usual spot (0). Saved with Save; Ctrl+Z undoes."""
        meta = self._tl_meta(key)
        if meta is None:
            return
        segs, _data = self._tl_segments(meta["files"])
        saved = (segs[meta["i"]] if meta["i"] < len(segs) else {}).get("layer")
        new = dict(self._edits)
        lk = self._layer_key(meta)
        if int(layer or 0) == int(saved or 0):
            new.pop(lk, None)
        else:
            new[lk] = int(layer) if layer else None if saved is None else 0
        if new != self._edits:
            self._push_state(new)
            self._apply_edit_state(new)
            self.status_var.set(("Caption stacked above the one below it" if layer else
                                 "Caption back in the usual spot") + ". Save to keep it (or Undo).")

    def _tl_caption_spans(self) -> dict:
        """line key -> (start, end) of its caption on screen, from the player's captions (built once
        per state of the files and edits; see _preview_caps)."""
        parts = getattr(self, "_pcaps_parts", None)
        if not parts or not getattr(self, "display", None) or self.display[0] != "saved":
            return {}
        try:
            self._preview_caps(parts)
        except Exception:
            return {}
        return getattr(self, "_pcaps_spans", None) or {}

    def _tl_seek(self, t, scrub: bool = False):
        """The playhead moved on the timeline (a click, or dragging it: scrub). The player follows;
        the transcript's blue row too, without scrolling to it while scrubbing."""
        playing = bool(getattr(self.player, "mp", None) and self.player.available and self.player.mp.is_playing())
        self.player.seek(t, play=playing and not scrub, quick=scrub)
        line = self._line_at_time(t)
        if line is not None:
            self._set_now(line)

    def _line_at_time(self, t):
        rows = getattr(self, "_rows", [])
        if not rows:
            return None
        i = bisect.bisect_right(rows, (t + 0.01, float("inf"))) - 1
        return rows[i][1] if i >= 0 else None

    def _tl_line_for(self, key):
        """The transcript line showing this block (or the folded paragraph holding it)."""
        for line, meta in self._row_meta.items():
            if meta.get("key") == key:
                return line
        for line, info in getattr(self, "_group_lines", {}).items():
            if not info["open"] and any(m.get("key") == key for m in info["metas"]):
                return line
        return None

    def _tl_select(self, key):
        line = self._tl_line_for(key)
        if line is not None:
            self._set_hover(line)
            self.text.see(f"{line}.0")

    def _tl_row(self, key):
        """The transcript row of a block, opening its paragraph if it's folded (or None)."""
        self._show_tab("transcript")
        line = self._tl_line_for(key)
        if line is None:
            return None
        if line not in self._row_meta:                    # inside a folded paragraph: open it first
            info = self._group_lines.get(line)
            if info:
                self._expanded.add(info["key"])
                self._rerender_in_place()
            line = self._tl_line_for(key)
            if line is None or line not in self._row_meta:
                return None
        self.text.see(f"{line}.0")
        return line

    def _tl_edit(self, key):
        line = self._tl_row(key)
        if line is not None:
            self._start_edit(line, f"b{line}e")

    def _tl_menu(self, key, x_root, y_root, in_range=False):
        """Right-click on the timeline: a line's short menu (edit, who's speaking, mark, delete; and
        Export clip when it's inside the marked stretch), or the marked stretch's own (play, export)."""
        rng = self._tl.range if (in_range and self._tl) else None
        if key is None:
            if not rng:
                return
            from transcriber.fonts import ui_font
            p = self._palette()
            dark = self.current_theme == "dark"
            menu = tk.Menu(self, tearoff=0, background="#2b2b2b" if dark else "#ffffff", foreground=p["fg"],
                           activebackground=p["hover"], activeforeground=p["fg"], relief="flat", borderwidth=1,
                           font=ui_font(10))
            menu.add_command(label="Play this part", command=lambda: self._tl_play_range(*rng))
            menu.add_command(label="Export clip...", command=lambda: self._tl_export_range(*rng))
            menu.add_separator()
            menu.add_command(label="Clear the marked part", command=self._tl.clear_range)
            try:
                menu.tk_popup(x_root, y_root)
            finally:
                menu.grab_release()
            return
        line = self._tl_row(key)
        if line is not None:
            self._set_hover(line)
            self._row_menu(line, x_root, y_root, short=True, clip_range=rng)

    def _edit_at_caption(self, cap):
        """A caption on the video was double-clicked: type on it right there (the player's editor),
        and the new words go to the transcript line it comes from, as a pending edit like any other.
        cap = (start, text, name, color, track, end, layer, line keys)."""
        if not self.display or self.display[0] != "saved":
            return
        meta = self._caption_meta(cap)
        if meta is None:
            return
        if self._tl is not None and self.timeline_open:
            self._tl.select(meta["key"])
        if self._editing is not None:
            self._end_edit()
        current = self._edits.get(meta["key"]) or meta["orig"] or ""
        self.player.start_caption_edit(cap, current, lambda new, m=meta: self._caption_typed(m, new))

    def _caption_typed(self, meta, new):
        """The caption editor closed: new words (or None for no change) for that transcript line."""
        if new is None or not new.strip():
            return
        line = self._tl_row(meta["key"])
        if line is None or line not in self._row_meta:          # not shown: straight into the edits
            state = dict(self._edits)
            if " ".join(new.split()) == " ".join((meta["orig"] or "").split()):
                state.pop(meta["key"], None)
            else:
                state[meta["key"]] = new
            if state != self._edits:
                self._push_state(state)
                self._apply_edit_state(state)
            return
        self._start_edit(line, f"b{line}e")
        t = self.text
        t.delete(f"b{line}s", f"b{line}e")
        t.insert(f"b{line}s", new)
        self._end_edit()
        self.status_var.set("Caption changed. Save to keep it (or Undo).")

    def _caption_meta(self, cap):
        """The transcript line a caption came from: by the line keys the caption carries, else the
        line on that track that starts at or before it and shares its words."""
        keys = cap[7] if len(cap) > 7 and cap[7] else ()
        if keys:
            metas = {m.get("key"): m for m in self._all_row_metas()}
            for key in keys:
                m = metas.get(tuple(key) if isinstance(key, list) else key)
                if m is not None and not self._is_deleted(m):
                    return m
        start, text = float(cap[0]), cap[1]
        track = cap[4] if len(cap) > 4 else ""
        want = set(w.strip(".,!?;:\"'").lower() for w in text.split())
        best, best_score = None, None
        for meta in self._all_row_metas():
            if self._is_deleted(meta):
                continue
            s = float(self._shown_time(meta))
            if s > start + 0.05 or s < start - 60:
                continue
            words = (self._edits.get(meta["key"]) or meta["orig"] or "").split()
            have = set(w.strip(".,!?;:\"'").lower() for w in words)
            overlap = len(want & have) / max(len(want), 1)
            score = (not track or (meta.get("track") or "") == track, overlap > 0.5, s)
            if best_score is None or score > best_score:
                best, best_score = meta, score
        return best

    def _timeline_row_clicked(self, line):
        """A transcript row was clicked: select its block."""
        if self._tl is None or not self.timeline_open:
            return
        meta = self._row_meta.get(line)
        if meta is None:
            info = getattr(self, "_group_lines", {}).get(line)
            meta = info["metas"][0] if info and info["metas"] else None
        if meta is not None:
            self._tl.select(meta.get("key"))


def _fmt(t: float) -> str:
    from transcriber.timeline import fmt
    return fmt(t)
