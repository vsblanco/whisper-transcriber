"""Markers: flag good moments in a transcript. Press M on a row (or right-click > Mark) and a thin
colored line appears on the row's right edge. Claude can add markers too (orange), with a note
saying why; hover a marked row to read it.

Markers live in '<transcript name>_markers.json' next to the transcript:
    {"version": 1, "markers": [{"time": 83.4, "track": "Mic", "text": "the line", "by": "you" | "claude",
                                "note": "why it's worth a look"}]}
A marker can also cover a stretch: "end" (seconds) makes it a range, shown on every row it covers,
with the exact words tinted. Select text and press M to mark exactly those words.
A marker belongs to the row it starts in (same track; its text, when given, picks between rows
starting at the same time). The app notices when the file changes, so markers written while it's
open show up within a few seconds.

A mixin for the App class in app.py."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional
import tkinter as tk
from tkinter import ttk

from transcriber.core import AUDIO_EXTENSIONS, write_text_safe
from transcriber.fonts import ui_font
from transcriber.icons import info_icon
from transcriber.ui_common import set_dark_titlebar

COLORS = {"you": "#4c8dff", "claude": "#d97757"}      # yours: blue; Claude's: orange
TINTS = {"light": {"you": "#dbe7ff", "claude": "#f8dfd4"}, "dark": {"you": "#1f3355", "claude": "#4a2c22"}}
WIDTH = 4


def markers_path_for(files: dict) -> Optional[Path]:
    """'<name>_markers.json' for a version's files (see names.markers_path)."""
    from transcriber.names import markers_path
    return markers_path(files)


def read_markers(path) -> list:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")).get("markers") or []
    except (OSError, ValueError, AttributeError):
        return []


def write_markers(path, markers: list) -> None:
    write_text_safe(path, json.dumps({"version": 1, "markers": markers}, indent=1))


class MarkersMixin:

    def _markers_setup(self):
        self._marker_file: Optional[Path] = None
        self._marker_stamp = None
        self._marker_rows: dict = {}          # text line -> marker dict
        for who, color in COLORS.items():
            self.text.tag_configure(f"mark_{who}", rmargin=WIDTH, rmargincolor=color)
        self.text.bind("<Key-m>", self._mark_key)
        self.text.bind("<Key-M>", self._mark_key)
        self.text.bind("<Motion>", self._marker_hover, add="+")

    def _markers_theme(self):
        tints = TINTS.get(self.current_theme or "light", TINTS["light"])
        for who in COLORS:
            self.text.tag_configure(f"mspan_{who}", background=tints[who])
        for tag in ("mark_you", "mark_claude", "mspan_you", "mspan_claude"):
            self.text.tag_raise(tag)

    # ---- which file, and showing them

    def _markers_after_render(self):
        """Called after the transcript is drawn: find its markers file and mark the rows."""
        self._marker_file = None
        metas = list(getattr(self, "_row_meta", {}).values())
        if metas:
            self._marker_file = markers_path_for(metas[0]["files"])
        self._show_markers()

    def _show_markers(self):
        t = self.text
        for tag in ("mark_you", "mark_claude", "mspan_you", "mspan_claude"):
            t.tag_remove(tag, "1.0", "end")
        self._marker_rows = {}
        self._marker_cover = {}
        f = self._marker_file
        markers = []
        if f:
            try:
                self._marker_stamp = os.path.getmtime(f)
                markers = read_markers(f)
            except OSError:
                self._marker_stamp = None
        self._update_export_btn(bool(markers))
        for m in markers:
            line = self._marker_line(m)
            if line is None:
                continue
            self._marker_rows.setdefault(line, m)
            who = "claude" if m.get("by") == "claude" else "you"
            t.tag_add(f"mark_{who}", f"{line}.0", f"{line}.0 lineend +1c")
            if m.get("end") and line in self._row_meta:
                self._show_range(m, line, who)
        self._markers_theme()

    def _show_range(self, m, line, who):
        """A range marker: the margin line on every row it covers, and its words tinted."""
        from transcriber.words import word_spans
        t = self.text
        a, b = float(m["time"]), float(m["end"])
        track = self._row_meta[line].get("track") or ""
        for ln, meta in self._row_meta.items():
            if (meta.get("track") or "") != track:
                continue
            start = float(meta.get("sec", 0))
            if ln != line and not (a < start < b):
                continue
            if ln != line:
                t.tag_add(f"mark_{who}", f"{ln}.0", f"{ln}.0 lineend +1c")
                self._marker_cover.setdefault(ln, m)
            wf = meta["files"].get("words")
            lines = self._words_for(wf) if wf else None
            try:
                cur = t.get(f"b{ln}s", f"b{ln}e")
            except tk.TclError:
                continue
            if lines and meta["i"] < len(lines) and cur == meta["orig"]:
                inside = [(c0, c1) for c0, c1, w in word_spans(cur, lines[meta["i"]])
                          if a - 0.02 <= (w[0] + w[1]) / 2 <= b + 0.02]
                if inside:            # one continuous highlight from the first word to the last
                    t.tag_add(f"mspan_{who}", f"b{ln}s + {inside[0][0]} chars", f"b{ln}s + {inside[-1][1]} chars")

    def _update_export_btn(self, show: bool):
        b = getattr(self, "export_btn", None)
        if b is None:
            return
        if show and not b.winfo_ismapped():
            b.pack(side="right", padx=(0, 6), after=self.history_btn)
        elif not show and b.winfo_ismapped():
            b.pack_forget()

    def _marker_line(self, m) -> Optional[int]:
        """The row a marker belongs to: the row starting at its time with its text, else the row
        it falls in (the last one starting at or before it)."""
        t = float(m.get("time", -1))
        best, best_sec = None, None
        candidates = list(self._row_meta.items()) + [(gl, meta) for gl, info in getattr(self, "_group_lines", {}).items()
                                                     if not info["open"] for meta in info["metas"]]
        for line, meta in candidates:
            if m.get("track") and meta.get("track") and m["track"] != meta["track"]:
                continue
            sec = float(meta.get("sec", -99))
            if abs(sec - t) <= 1.01 and m.get("text") and m["text"].strip() == meta["orig"].strip():
                return line
            if sec <= t + 1.01 and (best_sec is None or sec > best_sec or (sec == best_sec and line < best)):
                best, best_sec = line, sec
        return best

    def _check_markers(self):
        """The markers file changed on disk (e.g. Claude added some): show them again."""
        f = self._marker_file
        if not f:
            return
        try:
            stamp = os.path.getmtime(f)
        except OSError:
            stamp = None
        if stamp != self._marker_stamp:
            self._show_markers()

    # ---- adding and removing

    def _toggle_mark(self, line: int):
        meta = self._row_meta.get(line)
        f = self._marker_file
        if not meta or not f:
            self.bell()
            return
        markers = read_markers(f)
        existing = self._marker_rows.get(line)
        if existing:
            markers = [m for m in markers if not (m.get("time") == existing.get("time")
                                                  and m.get("track", "") == existing.get("track", "")
                                                  and m.get("by") == existing.get("by"))]
            self.status_var.set("Marker removed.")
        else:
            markers.append({"time": round(float(meta.get("sec", 0)), 2), "track": meta.get("track") or "",
                            "text": meta["orig"], "by": "you", "note": ""})
            markers.sort(key=lambda m: m.get("time", 0))
            self.status_var.set("Marked. Press M again to remove.")
        try:
            write_markers(f, markers)
        except OSError as exc:
            self.status_var.set(f"Couldn't save the marker: {exc}")
            return
        self._show_markers()

    def _mark_key(self, e):
        if getattr(self, "_editing", None) is not None:
            return self._edit_key(e)         # typing an "m" while editing a line
        if getattr(self, "_time_entry", None):
            return None
        if self.text.tag_ranges("sel"):
            self._mark_selection()
            return "break"
        line = self._hover_line if self._hover_line in self._row_meta else self._now_line
        if line in self._row_meta:
            self._toggle_mark(line)
        return "break"

    def _mark_selection(self):
        """Mark exactly the selected words (or rows) as a range."""
        from transcriber.words import word_at
        t = self.text
        if not t.tag_ranges("sel") or not self._marker_file:
            self.bell()
            return
        s_idx, e_idx = t.index("sel.first"), t.index("sel.last")
        rows = [ln for ln in sorted(self._row_meta) if int(s_idx.split(".")[0]) <= ln <= int(e_idx.split(".")[0])]
        if not rows:
            self.bell()
            return
        l0, l1 = rows[0], rows[-1]

        def word_time(line, index, end: bool):
            meta = self._row_meta[line]
            wf = meta["files"].get("words")
            lines = self._words_for(wf) if wf else None
            cur = t.get(f"b{line}s", f"b{line}e")
            if not lines or meta["i"] >= len(lines) or cur != meta["orig"]:
                return None
            if t.compare(index, "<", f"b{line}s") or t.compare(index, ">", f"b{line}e"):
                return None
            off = len(t.get(f"b{line}s", index)) - (1 if end else 0)
            w = word_at(lines[meta["i"]], cur, max(off, 0))
            return (w[1] if end else w[0]) if w else None
        start = word_time(l0, s_idx, False)
        end = word_time(l1, e_idx, True)
        if start is None:
            start = float(self._row_meta[l0].get("sec", 0))
        if end is None:
            end = self._row_bounds(l1)[1]
        if end <= start:
            end = start + 0.5
        meta = self._row_meta[l0]
        markers = read_markers(self._marker_file)
        markers.append({"time": round(start, 2), "end": round(end, 2), "track": meta.get("track") or "",
                        "text": meta["orig"], "by": "you", "note": ""})
        markers.sort(key=lambda m: m.get("time", 0))
        try:
            write_markers(self._marker_file, markers)
        except OSError as exc:
            self.status_var.set(f"Couldn't save the marker: {exc}")
            return
        t.tag_remove("sel", "1.0", "end")
        self._show_markers()
        self.status_var.set(f"Marked {self._clock(start)} to {self._clock(end)}. Press M on the row to remove it.")

    @staticmethod
    def _clock(sec: float) -> str:
        m, s = divmod(max(sec, 0.0), 60)
        return f"{int(m)}:{s:04.1f}"

    def _marker_hover(self, e):
        line = int(self.text.index(f"@{e.x},{e.y}").split(".")[0])
        m = self._marker_rows.get(line) or getattr(self, "_marker_cover", {}).get(line)
        if not m:
            return
        span = f"{self._clock(float(m['time']))} to {self._clock(float(m['end']))}" if m.get("end") else ""
        if m.get("note"):
            who = "Claude" if m.get("by") == "claude" else "Your note"
            self.status_var.set(f"{who}" + (f" ({span})" if span else "") + f": {m['note']}")
        elif span:
            self.status_var.set(f"Marked {span}.")

    # ---- export to video editors

    def _export_markers_menu(self):
        p = self._palette()
        dark = self.current_theme == "dark"
        colors = dict(background="#2b2b2b" if dark else "#ffffff", foreground=p["fg"],
                      activebackground=p["hover"], activeforeground=p["fg"], disabledforeground=p["muted"],
                      font=ui_font(10), relief="flat", borderwidth=1)
        menu = tk.Menu(self, tearoff=0, **colors)
        menu.add_command(label="Export markers for...", state="disabled")
        resolve = tk.Menu(menu, tearoff=0, **colors)
        resolve.add_command(label="Timeline starts at 01:00:00:00 (Resolve's default)",
                            command=lambda: self._export_markers("resolve", 1))
        resolve.add_command(label="Timeline starts at 00:00:00:00", command=lambda: self._export_markers("resolve", 0))
        menu.add_command(label="DaVinci Resolve: audio + markers + captions", command=lambda: self._export_markers("resolve_all"))
        menu.add_command(label="DaVinci Resolve: send captions to the open timeline...", command=self._send_to_resolve_dialog)
        menu.add_cascade(label="DaVinci Resolve: markers only (.edl)", menu=resolve)
        menu.add_command(label="Final Cut Pro (.fcpxml)", command=lambda: self._export_markers("fcp"))
        menu.add_command(label="Premiere Pro (.xml)", command=lambda: self._export_markers("premiere"))
        menu.add_separator()
        menu.add_command(label="Video with captions (subtitle track)...", command=self._save_with_captions)
        menu.add_command(label="Cut video: only the marked parts, or without deleted lines...", command=self._cut_dialog)
        menu.add_separator()
        menu.add_command(label="Check captions (reading speed, timing, loops)...", command=self._check_captions)
        b = self.export_btn
        try:
            menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())
        finally:
            menu.grab_release()

    def _send_to_resolve_dialog(self):
        """Captions straight onto the timeline open in DaVinci Resolve: as a subtitle track, or as
        Text+ titles in each person's color."""
        if not getattr(self, "display", None) or self.display[0] != "saved":
            self.status_var.set("Pick a transcript first.")
            return
        win = tk.Toplevel(self)
        win.title("Send to DaVinci Resolve")
        win.transient(self)
        win.resizable(False, False)
        f = ttk.Frame(win, padding=(22, 18))
        f.pack(fill="both", expand=True)
        head = ttk.Frame(f)
        head.pack(anchor="w", pady=(0, 10))
        ttk.Label(head, text="Send captions to DaVinci Resolve", font=ui_font(12, bold=True)).pack(side="left")
        info_icon(head, self, "Puts the captions on the timeline that's open in Resolve, through Resolve's own "
                              "scripting. Resolve must be open with that timeline on the Edit page, and "
                              "Preferences > System > General > External scripting using must be Local.").pack(side="left", padx=6)
        how = tk.StringVar(value="text")
        ttk.Radiobutton(f, text="Text+ titles, one per caption, in each person's color", value="text", variable=how).pack(anchor="w")
        ttk.Label(f, text="Needs a Text+ in Resolve's media pool (drag Text+ from Effects > Titles into it, and style "
                          "it the way the captions should look). They go on a new video track.",
                  style="Muted.TLabel", wraplength=420, justify="left").pack(anchor="w", padx=(22, 0))
        ttk.Radiobutton(f, text="Subtitle track (Resolve's own captions)", value="track", variable=how).pack(anchor="w", pady=(8, 0))
        ttk.Label(f, text="One style for all, set in the Inspector. Works without any setup.",
                  style="Muted.TLabel", wraplength=420, justify="left").pack(anchor="w", padx=(22, 0))
        colors_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Each person's color on their captions (Text+ only)", variable=colors_var).pack(anchor="w", pady=(10, 0))
        status = ttk.Label(f, text="", style="Muted.TLabel", wraplength=420, justify="left")
        status.pack(anchor="w", pady=(8, 0))
        buttons = ttk.Frame(f)
        buttons.pack(fill="x", pady=(14, 0))

        def go():
            plan = self._caption_plan()
            source = (self._shown_item() or {}).get("source")
            language = self._transcript_language()
            win.destroy()
            self._send_to_resolve(how.get(), plan, source, language, bool(colors_var.get()))

        ttk.Button(buttons, text="Send", style="Accent.TButton", command=go).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 8))
        win.bind("<Return>", lambda e: go())
        win.bind("<Escape>", lambda e: win.destroy())
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_reqheight()) // 3
        win.geometry(f"+{x}+{y}")
        set_dark_titlebar(win, self.current_theme == "dark")
        win.grab_set()

    def _send_to_resolve(self, how: str, plan: list, source, language: str, colors: bool):
        import tempfile
        import threading
        from transcriber import resolve_bridge as rb
        from transcriber.captions import to_srt
        self.status_var.set("Lining the captions up with the voice...")
        self._busy(True, "Sending to Resolve")

        def say(msg):
            self.events.put(("call", lambda: self.status_var.set(msg)))

        def work():
            msg = ""
            try:
                cues = self.caption_cues(plan, source)
                if not cues:
                    raise rb.ResolveError("This transcript has no lines to send.")
                say("Talking to DaVinci Resolve...")
                info = rb.open_timeline(rb.connect())
                if how == "track":
                    tmp = tempfile.mkdtemp(prefix="wt_resolve_")
                    srt = Path(tmp, "captions.srt")
                    srt.write_text(to_srt(cues), encoding="utf-8")
                    rb.send_subtitle_track(info, str(srt))
                    msg = f"Captions are on “{info['name']}” in Resolve as a subtitle track ({len(cues)} captions)."
                else:
                    r = rb.send_text_plus(info, cues, colors=colors, size=None, progress=say)
                    msg = (f"{r['placed']} Text+ captions are on “{info['name']}” in Resolve (video track {r['track']})."
                           + ("" if r["styled"] == r["placed"] else
                              f" {r['placed'] - r['styled']} couldn't be styled: the media pool clip isn't a Text+."))
            except rb.ResolveError as exc:
                msg = str(exc)
            except Exception as exc:
                msg = f"Couldn't send to Resolve: {exc}"
            self.events.put(("call", lambda: (self._busy(False), self.status_var.set(msg))))

        threading.Thread(target=work, daemon=True).start()

    def _export_markers(self, kind: str, start_hour: int = 1):
        from tkinter import filedialog
        from transcriber import marker_export as mx
        from transcriber.core import has_video_stream, media_duration
        f = self._marker_file
        markers = read_markers(f) if f else []
        if not markers:
            self.bell()
            return
        title = f.name[: -len("_markers.json")]
        source = getattr(self, "_export_source", None)
        if kind == "resolve_all":
            return self._export_resolve_package(markers, title, source)
        if kind != "resolve" and not source:
            self.status_var.set("This transcript doesn't say which file it came from, so it can't be exported "
                                "for this editor. Try the Resolve export.")
            return
        ext = {"resolve": ".edl", "fcp": ".fcpxml", "premiere": ".xml"}[kind]
        out = filedialog.asksaveasfilename(parent=self, title="Export markers", initialdir=str(f.parent),
                                           initialfile=f"{title}_markers{ext}", defaultextension=ext,
                                           filetypes=[(ext.upper().lstrip(".") + " file", "*" + ext)])
        if not out:
            return
        here = bool(source) and os.path.exists(source)
        fps = mx.frame_rate(source if here else None)
        if kind == "resolve":
            data = mx.to_edl(markers, title, fps, start_hour)
        else:
            video = has_video_stream(source) if here else Path(source).suffix.lower() not in AUDIO_EXTENSIONS
            duration = (media_duration(source) if here else None) or max(float(m.get("time", 0)) for m in markers) + 60
            make = mx.to_fcpxml if kind == "fcp" else mx.to_xmeml
            data = make(markers, title, source, fps, duration, video)
        try:
            Path(out).write_text(data, encoding="utf-8", newline="")
        except OSError as exc:
            self.status_var.set(f"Couldn't export: {exc}")
            return
        self.status_var.set(f"Exported {len(markers)} markers to {Path(out).name}.")

    def _export_resolve_package(self, markers, title, source):
        """For DaVinci Resolve: a timeline file with the original audio/video and the markers on it
        (.fcpxml), plus the captions (.srt) next to it, and how to bring both in."""
        from tkinter import filedialog
        from transcriber import marker_export as mx
        from transcriber.core import has_video_stream, media_duration
        if not source:
            self.status_var.set("This transcript doesn't say which file it came from, so Resolve can't find the audio.")
            return
        out = filedialog.asksaveasfilename(parent=self, title="Export for DaVinci Resolve",
                                           initialdir=str(self._marker_file.parent), initialfile=f"{title}.fcpxml",
                                           defaultextension=".fcpxml", filetypes=[("Final Cut XML (Resolve reads it)", "*.fcpxml")])
        if not out:
            return
        plan = self._caption_plan()
        self.status_var.set("Exporting for DaVinci Resolve (lining the captions up with the voice)...")
        self._busy(True, "Exporting for Resolve")

        def work():
            err, caps = None, []
            try:
                here = os.path.exists(source)
                fps = mx.frame_rate(source if here else None)
                video = has_video_stream(source) if here else Path(source).suffix.lower() not in AUDIO_EXTENSIONS
                caps = self.caption_cues(plan, source if (here and video) else None)
                duration = (media_duration(source) if here else None) or max(
                    [float(m.get("end") or m.get("time", 0)) for m in markers] + [c.end for c in caps] + [0]) + 5
                Path(out).write_text(mx.to_fcpxml(markers, title, source, fps, duration, video), encoding="utf-8",
                                     newline="")
                # Resolve doesn't always show markers that come inside the timeline file, so they also go
                # in an EDL it reads as timeline markers (same timeline start: 00:00:00:00).
                edl.write_text(mx.to_edl(markers, title, fps, 0), encoding="utf-8", newline="")
                if caps:
                    from transcriber.captions import to_srt
                    srt.write_text(to_srt(caps), encoding="utf-8")
            except OSError as exc:
                err = str(exc)
            self.events.put(("call", lambda: (self._busy(False), self._resolve_exported(out, edl, srt, caps, source, err))))

        srt = Path(out).with_suffix(".srt")
        edl = Path(out).with_name(Path(out).stem + "_markers.edl")
        threading.Thread(target=work, daemon=True).start()

    def _resolve_exported(self, out, edl, srt, caps, source, err):
        if err:
            self.status_var.set(f"Couldn't export: {err}")
            return
        made = [Path(out).name, edl.name] + ([srt.name] if caps else [])
        self.status_var.set("Exported " + ", ".join(made) + f" to {Path(out).parent}")
        self._confirm(title="Exported for DaVinci Resolve", heading="Now bring it into Resolve",
                      body="Saved in " + str(Path(out).parent) + ":", items=made + [
                          "1. File > Import > Timeline..., pick " + Path(out).name
                          + ' (keep "Automatically import source clips" on). This makes a timeline with the audio.',
                          "2. In the Media Pool, right-click that timeline > Timelines > Import > Timeline Markers "
                          "from EDL..., pick " + edl.name + " (not File > Import, which makes a new timeline)",
                          ("3. File > Import > Subtitle..., pick " + srt.name
                           + ", then drag it from the Media Pool onto the start of the timeline") if caps else
                          "3. (No captions: this transcript has no lines)"],
                      detail="If Resolve can't find the media, it's at:\n" + source, confirm="OK", cancel=None)
