"""Tools for checking and fixing a transcript:

- Made-up text: words Whisper wrote with no sound under them are crossed out in gray, and can be
  removed with one click (right-click a row).
- Redo lines: transcribe just the selected rows again, with any model, as a new version.
- Export clip: save a marked moment (or any rows) as its own video or audio file, optionally
  with the captions burned in.
- Word timings for versions that have none (e.g. a Claude version): borrowed from another version
  of the same audio, so clicking a word still jumps to it.

A mixin for the App class in app.py."""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Optional

from transcriber.fonts import CAPTION_FONT, ui_font
from transcriber.core import _NO_WINDOW, AUDIO_EXTENSIONS
from transcriber.icons import info_icon
from transcriber.ui_common import set_dark_titlebar

MADE_UP_TIP = ("Probably not said: there's no sound under these words (Whisper sometimes makes text up). "
               "Right-click the row > Remove made-up words.")


def _fmt(sec: float) -> str:
    sec = max(sec, 0.0)
    m, s = divmod(sec, 60)
    h, m = divmod(int(m), 60)
    return (f"{h}:{m:02d}:{s:04.1f}" if h else f"{m}:{s:04.1f}")


def _parse(text: str) -> Optional[float]:
    try:
        parts = [float(x) for x in text.strip().replace(",", ".").split(":")]
    except ValueError:
        return None
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


class ReviewMixin:

    # ---------------------------------------------------------------- made-up text

    def _suspects_after_render(self):
        from transcriber.words import suspect_spans
        t = self.text
        p = self._palette()
        t.tag_configure("madeup", foreground=p["muted"], overstrike=True)
        t.tag_remove("madeup", "1.0", "end")
        self._suspect_rows = {}
        for line, meta in getattr(self, "_row_meta", {}).items():
            wf = meta["files"].get("words")
            lines = self._words_for(wf) if wf else None
            if not lines or meta["i"] >= len(lines):
                continue
            try:
                cur = t.get(f"b{line}s", f"b{line}e")
            except tk.TclError:
                continue
            if cur != meta["orig"]:
                continue
            spans = suspect_spans(cur, lines[meta["i"]])
            for a, b in spans:
                t.tag_add("madeup", f"b{line}s + {a} chars", f"b{line}s + {b} chars")
            if spans:
                self._suspect_rows[line] = spans

    def _suspect_hover(self, e):
        if "madeup" in self.text.tag_names(f"@{e.x},{e.y}"):
            self.status_var.set(MADE_UP_TIP)

    def _remove_madeup(self, lines):
        from transcriber.words import remove_spans
        if self._editing is not None:
            self._end_edit()
        new = dict(self._edits)
        n = 0
        for line in lines:
            meta, spans = self._row_meta.get(line), self._suspect_rows.get(line)
            if not meta or not spans or self._is_deleted(meta):
                continue
            cleaned = remove_spans(meta["orig"], spans)
            new[meta["key"]] = cleaned if cleaned.strip(" .,!?") else None
            n += 1
        if not n:
            return
        self._push_state(new)
        self._apply_edit_state(new)
        self.status_var.set(f"Removed made-up words from {n} line{'s' if n != 1 else ''}. Save to keep it.")

    # ---------------------------------------------------------------- word timings for versions without

    def _ensure_lines(self, item):
        """Older versions get their master list of lines (made once, when first shown), so every
        edit goes through one list and the formats stay identical."""
        from transcriber.edits import ensure_lines_file
        for versions in item.get("tracks", {}).values():
            for v in versions:
                if not v["files"].get("speakers"):
                    try:
                        v["files"].update(ensure_lines_file(v["files"]))
                    except OSError:
                        pass

    def _ensure_words(self, item):
        """Give versions without word timings (a Claude version, an old file) timings borrowed from
        the other versions of the same track."""
        from transcriber.words import retime_lines, words_path, write_words
        from transcriber.files import read_segments
        for versions in item.get("tracks", {}).values():
            pool = None
            for v in versions:
                f = v["files"]
                anchor = f.get("txt") or f.get("srt") or f.get("vtt")
                if f.get("words") or not anchor:
                    continue
                try:
                    miss_key = (str(anchor), os.path.getmtime(anchor))
                except OSError:
                    continue
                misses = self.__dict__.setdefault("_retime_misses", set())
                if miss_key in misses:          # tried before and nothing lined up: don't redo it every time
                    continue
                if pool is None:
                    pool = [w for o in versions if o["files"].get("words")
                            for ln in (self._words_for(o["files"]["words"]) or []) for w in ln]
                if not pool:
                    break
                rows = None
                if f.get("speakers"):          # its lines without the names in front
                    from transcriber.speakers import read_speakers_file
                    data = read_speakers_file(f["speakers"]) or {}
                    rows = [(float(x["start"]), float(x["end"]), x["text"]) for x in data.get("segments", [])]
                if not rows:
                    names = {p["name"].lower() for p in self.people.people}
                    rows = []
                    for sg in read_segments(f):
                        who, sep, rest = sg.text.partition(": ")
                        rows.append((sg.start, sg.end, rest if sep and who.lower() in names else sg.text))
                lines = retime_lines(rows, pool)
                if not any(lines):
                    misses.add(miss_key)
                    continue
                wp = words_path(anchor)
                try:
                    write_words(wp, lines)
                    f["words"] = wp
                except OSError:
                    pass

    # ---------------------------------------------------------------- helpers for row ranges

    def _selected_rows(self, line=None) -> list:
        """The rows covered by the text selection, else just `line`."""
        t = self.text
        rows = []
        if t.tag_ranges("sel"):
            a = int(t.index("sel.first").split(".")[0])
            b = int(t.index("sel.last").split(".")[0])
            rows = [ln for ln in sorted(self._row_meta) if a <= ln <= b]
        if line is not None and line not in rows:
            rows = [line]
        return rows

    def _row_bounds(self, line) -> tuple:
        """(start, end) seconds of a row: its start, and the next row's start (same track)."""
        meta = self._row_meta[line]
        start = float(meta.get("sec", 0))
        later = sorted(float(m.get("sec", 0)) for ln, m in self._row_meta.items()
                       if (m.get("track") or "") == (meta.get("track") or "") and float(m.get("sec", 0)) > start)
        end = later[0] if later else None
        wf = meta["files"].get("words")
        lines = self._words_for(wf) if wf else None
        if lines and meta["i"] < len(lines) and lines[meta["i"]]:
            end = max(lines[meta["i"]][-1][1], start + 0.5) if end is None else min(end, lines[meta["i"]][-1][1] + 0.4)
        return start, (end if end is not None else start + 5.0)

    def _shown_item(self):
        d = getattr(self, "display", None)
        if d and d[0] == "saved":
            return self.saved_items.get(d[1])
        return None

    # ---------------------------------------------------------------- export clip

    def _clip_dialog(self, line):
        item = self._shown_item()
        source = item.get("source") if item else None
        if not source or not os.path.exists(source):
            self.status_var.set("The original file is needed to export a clip, and it isn't here.")
            return
        rows = self._selected_rows(line)
        start, _ = self._row_bounds(rows[0])
        _, end = self._row_bounds(rows[-1])
        m = getattr(self, "_marker_rows", {}).get(line)
        if m and m.get("end"):
            start, end = float(m["time"]), float(m["end"])
        start, end = max(start - 0.3, 0.0), end + 0.3
        video = Path(source).suffix.lower() not in AUDIO_EXTENSIONS and not getattr(self.player, "_is_audio_file", False)

        win = tk.Toplevel(self)
        win.title("Export clip")
        win.transient(self)
        win.resizable(False, False)
        f = ttk.Frame(win, padding=(22, 18))
        f.pack(fill="both", expand=True)
        head = ttk.Frame(f)
        head.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 12))
        ttk.Label(head, text="Export clip", font=ui_font(12, bold=True)).pack(side="left")
        info_icon(head, self, "Saves this part of the original file as its own "
                              + ("video (mp4)" if video else "audio file (mp3)") + ". Times are minutes:seconds.").pack(
            side="left", padx=6)
        s_var, e_var = tk.StringVar(value=_fmt(start)), tk.StringVar(value=_fmt(end))
        ttk.Label(f, text="From").grid(row=1, column=0, sticky="w")
        ttk.Entry(f, textvariable=s_var, width=10, justify="right").grid(row=1, column=1, sticky="w", padx=(8, 16))
        ttk.Label(f, text="To").grid(row=1, column=2, sticky="w")
        ttk.Entry(f, textvariable=e_var, width=10, justify="right").grid(row=1, column=3, sticky="w", padx=(8, 0))
        caps_var = tk.BooleanVar(value=video)
        cr = ttk.Frame(f)
        cr.grid(row=2, column=0, columnspan=4, sticky="w", pady=(12, 0))
        ttk.Checkbutton(cr, text="Burn in captions", variable=caps_var,
                        state="normal" if video else "disabled").pack(side="left")
        info_icon(cr, self, "Puts the transcript's lines on the video, white text on a dark box, with the "
                            "speaker's name. They can't be turned off afterwards.").pack(side="left", padx=4)
        sound = self._clip_sound_options(item, source)
        sound_vars = []
        if sound:
            sb = ttk.Frame(f)
            sb.grid(row=3, column=0, columnspan=4, sticky="w", pady=(12, 0))
            hdr = ttk.Frame(sb)
            hdr.pack(anchor="w")
            ttk.Label(hdr, text="Sound").pack(side="left")
            info_icon(hdr, self, "Which mics or audio tracks to put in the clip, mixed together. It starts with "
                                 "what you're hearing in the player.").pack(side="left", padx=4)
            for opt in sound:
                v = tk.BooleanVar(value=opt["on"])
                ttk.Checkbutton(sb, text=opt["label"], variable=v).pack(anchor="w", padx=(10, 0))
                sound_vars.append((opt, v))
        status = ttk.Label(f, text="", style="Muted.TLabel")
        status.grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 0))
        buttons = ttk.Frame(f)
        buttons.grid(row=5, column=0, columnspan=4, sticky="e", pady=(14, 0))

        def go():
            a, b = _parse(s_var.get()), _parse(e_var.get())
            if a is None or b is None or b <= a:
                status.configure(text="Check the times (minutes:seconds, the end after the start).")
                return
            ext = ".mp4" if video else ".mp3"
            base = item["key"]
            default = f"{base} clip {int(a // 60)}m{int(a % 60):02d}s{ext}"
            folder = Path(self._current_version(item)["files"].get("txt") or source).parent
            out = filedialog.asksaveasfilename(parent=win, title="Save clip", initialdir=str(folder),
                                               initialfile=default, defaultextension=ext,
                                               filetypes=[(ext.lstrip(".").upper(), "*" + ext)])
            if not out:
                return
            picked = [o for o, v in sound_vars if v.get()]
            if sound_vars and not picked:
                status.configure(text="Pick at least one sound to include.")
                return
            plan = self._caption_plan() if (video and caps_var.get()) else None
            win.destroy()
            self._export_clip(source, a, b, out, video, plan, picked or None)

        ttk.Button(buttons, text="Export", style="Accent.TButton", command=go).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 8))
        win.bind("<Return>", lambda e: go())
        win.bind("<Escape>", lambda e: win.destroy())
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_reqheight()) // 3
        win.geometry(f"+{x}+{y}")
        set_dark_titlebar(win, self.current_theme == "dark")
        win.grab_set()

    def _caption_plan(self) -> list:
        """What's needed to time the shown transcript's captions (gathered here, in the window's
        thread; the slow part runs in timed_captions): one group per version part, with the audio
        it came from: [{"audio", "offset", "rows": [(text, name, (start, end) from the words)]}]."""
        from transcriber.core import Segment
        from transcriber.timing import row_spans
        item = self._shown_item() or {}
        names = {}
        for cap in self.player.captions() or []:
            if len(cap) > 2:
                names[(int(cap[0]), cap[1])] = cap[2] or ""
        session = {}
        if item.get("session"):
            from transcriber.session import read_session
            session = {f["label"]: (f["path"], float(f.get("offset", 0)))
                       for f in (read_session(item["session"]) or {}).get("files") or []}
        groups: dict = {}
        for line in sorted(getattr(self, "_row_meta", {})):
            meta = self._row_meta[line]
            if self._is_deleted(meta):
                continue
            key = str(self._file_key(meta["files"]))
            if key not in groups:
                wf = meta["files"].get("words")
                audio, off = session.get(meta.get("track") or "", (item.get("source"), 0.0))
                groups[key] = {"audio": audio, "offset": off, "rows": [], "words": self._words_for(wf) if wf else None}
            g = groups[key]
            ws = g["words"][meta["i"]] if g["words"] and meta["i"] < len(g["words"]) else None
            sec = float(meta.get("sec", 0))
            span = row_spans([Segment(sec, sec + 3, meta["orig"])], [ws] if ws else None)[0]
            g["rows"].append((meta["orig"], names.get((int(sec), meta["orig"]), ""), span))
        for g in groups.values():
            g.pop("words", None)
            g["rows"].sort(key=lambda r: r[2][0])
        return list(groups.values())

    def timed_captions(self, plan: list) -> list:
        """[(start, end, text, name)] for every caption, lined up with the voice in its audio
        (can be slow on long files: run it off the window's thread)."""
        import os
        from transcriber.timing import snap, speech_mask
        cache = self.__dict__.setdefault("_mask_cache", {})
        out = []
        for g in plan:
            spans = [r[2] for r in g["rows"]]
            audio = g["audio"]
            if audio and os.path.exists(audio):
                try:
                    key = (audio, os.path.getmtime(audio))
                    if key not in cache:
                        cache[key] = speech_mask(audio)
                    spans = snap(spans, cache[key], g["offset"], [r[0] for r in g["rows"]])
                except Exception:
                    pass
            out += [(st, en, text, name) for (text, name, _s), (st, en) in zip(g["rows"], spans)]
        return sorted(out, key=lambda c: c[0])

    @staticmethod
    def captions_between(timed: list, a: float, b: float) -> list:
        """[(start, end, text)] relative to a..b. Names go in front only when more than one person
        talks (one voice: no names)."""
        names = {c[3] for c in timed if c[3]}
        out = []
        for st, en, text, name in timed:
            en = min(max(en, st + 0.8), st + 10)
            if en <= a or st >= b:
                continue
            who = name if name and len(names) > 1 else ""
            out.append((max(st, a) - a, min(en, b) - a, (f"{who}: " if who else "") + text))
        return out

    @staticmethod
    def _srt(caps: list) -> str:
        def ts(x):
            ms = int(round(x * 1000))
            return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
        return "\n".join(f"{i}\n{ts(s)} --> {ts(e)}\n{t}\n" for i, (s, e, t) in enumerate(caps, 1))

    def _clip_sound_options(self, item, source) -> list:
        """The mics (combined recording) or audio tracks (OBS-style file) a clip can include:
        [{"label", "file", "stream", "offset", "on"}], on = what the player is playing now."""
        playing = set()
        try:
            playing = set(self.player._enabled()) if self.player.tracks else set()
        except Exception:
            pass
        if item.get("session"):
            from transcriber.session import read_session, reference_file
            data = read_session(item["session"]) or {}
            ref = reference_file(data)
            files = [f for f in data.get("files") or [] if os.path.exists(f["path"])]
            if len(files) < 2:
                return []
            ref_off = next((float(f.get("offset", 0)) for f in files if f["path"] == ref), 0.0)
            return [{"label": f["label"] + ("   (camera)" if f["path"] == data.get("video") else ""),
                     "file": f["path"], "stream": None, "offset": float(f.get("offset", 0)) - ref_off,
                     "on": (i in playing) if playing else f.get("transcribed", True)}
                    for i, f in enumerate(files)]
        tracks = [t for t in (self.player.tracks or []) if "file" not in t]
        if len(tracks) < 2:
            return []
        return [{"label": t["name"] or f"Track {t['n'] + 1}", "file": None, "stream": t["n"], "offset": 0.0,
                 "on": (t["n"] in playing) if playing else True} for t in tracks]

    @staticmethod
    def clip_command(source, a, b, out, video, sources=None, srt=None, style="") -> list:
        """The ffmpeg command for a clip from a to b seconds. sources: the sounds to mix
        ({"file": other recording or None for the source's own track "stream", "offset": where that
        recording starts on the source's timeline}); None = the source's sound as it is."""
        dur = b - a
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", source]
        chains, labels, n_in = [], [], 1
        for src in sources or []:
            if src.get("file") and src["file"] != source:
                start = a - float(src.get("offset", 0))
                delay = 0.0
                if start < 0:                       # this mic started recording after the clip begins
                    delay, start = -start, 0.0
                if delay >= dur:
                    continue
                cmd += ["-ss", f"{start:.3f}", "-t", f"{dur - delay:.3f}", "-i", src["file"]]
                lab = f"s{n_in}"
                chains.append(f"[{n_in}:a]" + (f"adelay={int(delay * 1000)}:all=1," if delay else "")
                              + f"aresample=async=1[{lab}]")
                labels.append(lab)
                n_in += 1
            else:
                stream = src.get("stream")
                lab = f"t{len(labels)}"
                chains.append((f"[0:a:{stream}]" if stream is not None else "[0:a]") + f"anull[{lab}]")
                labels.append(lab)
        fc, amap = [], None
        if labels:
            fc += chains
            if len(labels) == 1:
                amap = f"[{labels[0]}]"
            else:
                fc.append("".join(f"[{x}]" for x in labels)
                          + f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0,volume={len(labels)}[aout]")
                amap = "[aout]"
        if video:
            vmap = "0:v:0"
            if srt:
                fc.append(f"[0:v:0]subtitles={srt}:force_style='{style}'[vout]")
                vmap = "[vout]"
            if fc:
                cmd += ["-filter_complex", ";".join(fc)]
            cmd += ["-map", vmap, "-map", amap or "0:a:0?"]
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart"]
        else:
            if fc:
                cmd += ["-filter_complex", ";".join(fc), "-map", amap]
            cmd += ["-vn", "-c:a", "libmp3lame", "-q:a", "2"]
        cmd.append(out)
        return cmd

    def _export_clip(self, source, a, b, out, video, plan, sources=None):
        self.status_var.set(f"Exporting the clip ({_fmt(a)} to {_fmt(b)})...")
        self._busy(True, "Exporting clip")

        def work():
            tmp = tempfile.mkdtemp(prefix="wt_clip_")
            srt = None
            caps = self.captions_between(self.timed_captions(plan), a, b) if plan else None
            if video and caps:
                Path(tmp, "clip.srt").write_text(self._srt(caps), encoding="utf-8")
                srt = "clip.srt"
            style = (f"FontName={CAPTION_FONT},FontSize=15,BorderStyle=3,Outline=6,Shadow=0,"
                     "OutlineColour=&H99000000,PrimaryColour=&H00FFFFFF,MarginV=22")
            cmd = self.clip_command(source, a, b, str(Path(out).resolve()), video, sources, srt, style)
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp, creationflags=_NO_WINDOW)
                tail = r.stderr.strip().splitlines()
                err = (tail[-1] if tail else f"ffmpeg stopped (code {r.returncode})") if r.returncode else ""
            except OSError as exc:
                err = str(exc)
            finally:
                try:
                    for p in Path(tmp).iterdir():
                        p.unlink()
                    os.rmdir(tmp)
                except OSError:
                    pass
            msg = (f"Couldn't export the clip: {err}" if err else f"Saved the clip as {Path(out).name}.")
            self.events.put(("call", lambda: (self._busy(False), self.status_var.set(msg))))

        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- line up times with the voice

    def _line_up_times(self, item):
        """Move every line's start and end to where the voice really starts and stops (Whisper can be
        a second off), in the shown version. Saved like an edit: there's a copy to go back to."""
        import os
        from transcriber.files import read_version
        from transcriber.timing import row_spans
        if self._edits:
            self.status_var.set("Save or discard your edits first.")
            return
        v = self._current_version(item)
        parts = list(v["parts"].items()) if v.get("parts") else [(self._track_key(item) if self._track_key(item) != self.TRACKS else "", v)]
        session = {}
        if item.get("session"):
            from transcriber.session import read_session
            session = {f["label"]: (f["path"], float(f.get("offset", 0)))
                       for f in (read_session(item["session"]) or {}).get("files") or []}
        jobs = []
        for track, pv in parts:
            files = dict(pv["files"])
            audio, off = session.get(track, (item.get("source"), 0.0))
            if not audio or not os.path.exists(audio):
                continue
            segs, _labels, _data = read_version(files)
            words = [list(sg.words) for sg in segs] if any(sg.words for sg in segs) else None
            jobs.append((files, audio, off, row_spans(segs, words), [sg.text for sg in segs],
                         [(sg.start, sg.end) for sg in segs]))
        if not jobs:
            self.status_var.set("The original recording is needed for this, and it isn't here.")
            return
        self.status_var.set("Lining the times up with the voice...")
        self._busy(True, "Lining up times")

        def work():
            from transcriber.edits import set_spans
            from transcriber.timing import snap, speech_mask
            err, moved = None, 0
            try:
                for files, audio, off, spans, texts, now in jobs:
                    new = snap(spans, speech_mask(audio), off, texts)
                    moved += sum(1 for (a, _b), (c, _d) in zip(now, new) if abs(a - c) > 0.2)
                    self._backup(files)
                    set_spans(files, new, self.people)
            except Exception as exc:
                err = str(exc) or type(exc).__name__
            self.events.put(("call", lambda: (self._busy(False), self._refresh_saved(force=True), self._redisplay(),
                                              self.status_var.set(f"Couldn't line up the times: {err}" if err else
                                                                  f"Lined up with the voice: {moved} line"
                                                                  f"{'s' if moved != 1 else ''} moved. (Earlier copies "
                                                                  "have the old times.)"))))
        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- redo lines

    def _redo_lines(self, line, model: str):
        """Transcribe the selected rows again with `model`, saved as a new version."""
        item = self._shown_item()
        if not item:
            return
        rows = self._selected_rows(line)
        meta0 = self._row_meta[rows[0]]
        rows = [r for r in rows if self._row_meta[r]["files"] is meta0["files"]
                or self._row_meta[r]["files"] == meta0["files"]]
        if self._edits:
            self.status_var.set("Save or discard your edits first.")
            return
        files, track = meta0["files"], meta0.get("track") or ""
        source, offset, tindex = item.get("source"), 0.0, None
        if item.get("session"):
            from transcriber.session import read_session
            for f in (read_session(item["session"]) or {}).get("files") or []:
                if f["label"] == track:
                    source, offset = f["path"], float(f.get("offset", 0))
        elif track:
            tindex = self._source_track_index(source, track)
        if not source or not os.path.exists(source):
            self.status_var.set("The original file is needed to redo lines, and it isn't here.")
            return
        idx = sorted(self._row_meta[r]["i"] for r in rows)
        a, _ = self._row_bounds(rows[0])
        _, b = self._row_bounds(rows[-1])
        clip = (max(a - 0.4 - offset, 0.0), b + 0.4 - offset)
        person = self._row_speaker(meta0) if hasattr(self, "_row_speaker") else ""
        prompt = self._vocab_prompt(person or "")
        lang = getattr(self, "_lang_code", lambda: None)()
        self.status_var.set(f"Redoing {len(idx)} line{'s' if len(idx) != 1 else ''} with the {model} model...")
        self._busy(True, "Redoing lines")

        def work():
            from transcriber.core import transcribe_file
            try:
                t = transcribe_file(source, model, lang, track=tindex, track_name=track, clip=clip, prompt=prompt,
                                    log=lambda m: self.events.put(("call", lambda m=m: self.status_var.set(m))))
                for s in t.segments:
                    s.start, s.end = s.start + offset, s.end + offset
                    s.words = [(x + offset, y + offset, w) for x, y, w in s.words]
                self.events.put(("call", lambda: (self._busy(False),
                                                  self._redo_save(item, files, track, idx, t.segments, model))))
            except Exception as exc:
                self.events.put(("call", lambda exc=exc: (self._busy(False),
                                                          self.status_var.set(f"Couldn't redo the lines: {exc}"))))

        threading.Thread(target=work, daemon=True).start()

    def _redo_save(self, item, files, track, idx, new_segs, model):
        from transcriber.core import Transcript
        from transcriber.files import read_header, read_version, save_transcript
        from transcriber.speakers import display_names
        segs, labels, data = read_version(files)
        if len(labels) != len(segs):
            labels = []
        if not segs or idx[-1] >= len(segs):
            self.status_var.set("The transcript changed while redoing, so nothing was saved. Try again.")
            return
        i0, i1 = idx[0], idx[-1]
        old = segs[i0:i1 + 1]
        lo = old[0].start - 1.0
        hi = (segs[i1 + 1].start if i1 + 1 < len(segs) else old[-1].end + 1.0)
        new_segs = [s for s in new_segs if s.text.strip() and lo <= s.start < hi]
        if not new_segs:
            self.status_var.set("Nothing was heard in those lines, so nothing changed.")
            return

        def label_for(s):           # whoever said the old line that overlaps the new one most
            best, lab = -1.0, None
            for k, o in enumerate(old):
                ov = min(o.end, s.end) - max(o.start, s.start)
                if ov > best:
                    best, lab = ov, labels[i0 + k] if labels else None
            return lab
        out_segs = segs[:i0] + new_segs + segs[i1 + 1:]
        out_labels = (labels[:i0] + [label_for(s) for s in new_segs] + labels[i1 + 1:]) if labels else []
        model_line = read_header(files.get("txt")).get("Model", "") if files.get("txt") else ""
        base_model = model_line.split(" + ")[0] or "?"
        spk = (data or {}).get("speakers") or {}
        t = Transcript(source=item.get("source") or "", model=f"{base_model} + {model} (redo)",
                       language=(data or {}).get("language", ""), segments=out_segs,
                       text=" ".join(s.text for s in out_segs), track=track, speakers=out_labels,
                       voiceprints={k: (v or {}).get("voiceprint") for k, v in spk.items()},
                       names=display_names(data, self.people) if data else {},
                       people_ids={k: (v or {}).get("person") for k, v in spk.items()})
        formats = [f for f in ("txt", "srt", "vtt") if files.get(f)] or ["txt"]
        anchor = Path(files.get("txt") or files.get("srt") or files.get("vtt"))
        try:
            if hasattr(self, "_backup"):
                self._backup(files)
            save_transcript(t, anchor.parent.parent, formats, name=item["key"])
        except OSError as exc:
            self.status_var.set(f"Couldn't save the new version: {exc}")
            return
        self.saved_version.pop(f"{item['key']}|{self._track_key(item)}", None)     # show the new one
        self._refresh_saved(force=True)
        self.status_var.set(f"Redid {len(idx)} line{'s' if len(idx) != 1 else ''} with {model}: saved as a new "
                            "version (the earlier one is still in the version list).")
