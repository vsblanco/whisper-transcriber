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
        later = sorted(float(m.get("sec", 0)) for m in self._all_row_metas()
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

    def _clip_dialog(self, line, start=None, end=None):
        """Export clip: a line's time (padded a little), a range marker's, or start..end as given
        (the stretch marked on the timeline)."""
        item = self._shown_item()
        source = item.get("source") if item else None
        if not source or not os.path.exists(source):
            self.status_var.set("The original file is needed to export a clip, and it isn't here.")
            return
        if start is None or end is None:
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
        caps_var = tk.StringVar(value=(self.caption_style or {}).get("clip_captions") or "both" if video else "off")
        cr = ttk.Frame(f)
        cr.grid(row=2, column=0, columnspan=4, sticky="w", pady=(12, 0))
        hdr = ttk.Frame(cr)
        hdr.pack(anchor="w")
        ttk.Label(hdr, text="Captions").pack(side="left")
        info_icon(hdr, self, "Burned in: drawn onto the picture with the look from Settings > Captions; every "
                             "player shows them, and they can't be turned off. Subtitle track: the captions go "
                             "inside the video as a track that VLC, TVs, YouTube and editors list under Subtitles "
                             "(on by default, can be turned off). Microsoft's Media Player app ignores tracks "
                             "inside mp4 files, so for it only burned-in captions show.").pack(side="left", padx=4)
        for value, label in (("both", "Burned in + subtitle track"), ("burn", "Burned in"), ("track", "Subtitle track only"), ("off", "None")):
            ttk.Radiobutton(cr, text=label, value=value, variable=caps_var,
                            state="normal" if video else "disabled").pack(anchor="w", padx=(10, 0))
        hl_var = tk.BooleanVar(value=bool((self.caption_style or {}).get("clip_highlight")))
        hr = ttk.Frame(cr)
        hr.pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(hr, text="Highlight each word as it's said", variable=hl_var,
                        state="normal" if video else "disabled").pack(side="left")
        info_icon(hr, self, "In the burned-in captions, the word being spoken lights up in the speaker's color "
                            "(yellow when there's no speaker), the way short-form videos do. Needs word timings.").pack(side="left", padx=4)
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
            mode = caps_var.get() if video else "off"
            plan = self._caption_plan() if mode != "off" else None
            if video:                                        # remembered for the next clip
                self.caption_style = {**(self.caption_style or {}), "clip_captions": mode,
                                      "clip_highlight": bool(hl_var.get())}
                from transcriber.ui_common import save_settings
                save_settings(self._settings())
            win.destroy()
            self._export_clip(source, a, b, out, video, plan, picked or None, mode, bool(hl_var.get()))

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
        """What's needed to make the shown transcript's captions (gathered here, in the window's
        thread; the slow part runs in caption_cues): one group per version part, with the audio it
        came from: [{"audio", "offset", "rows": [(text, name, (start, end), words, track, color)]}]."""
        from transcriber.core import Segment
        from transcriber.timing import row_spans
        item = self._shown_item() or {}
        names = {}
        for cap in self.player.captions() or []:          # the per-line captions (names and colors)
            if len(cap) > 2:
                names[(int(cap[0]), cap[1])] = (cap[2] or "", cap[3] if len(cap) > 3 else "")
        session = {}
        if item.get("session"):
            from transcriber.session import read_session
            session = {f["label"]: (f["path"], float(f.get("offset", 0)))
                       for f in (read_session(item["session"]) or {}).get("files") or []}
        groups: dict = {}
        metas = self._all_row_metas()
        tracks = {m.get("track") or "" for m in metas}
        for meta in metas:
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
            seg = self._tl_segments(meta["files"])[0] if hasattr(self, "_tl_segments") else []
            seg = seg[meta["i"]] if meta["i"] < len(seg) else {}
            fixed = bool(seg.get("fixed"))           # set on the timeline: kept exactly, not lined up again
            if fixed:
                span = (float(seg.get("start", sec)), float(seg.get("end", span[1])))
            name, color = names.get((int(sec), meta["orig"]), ("", ""))
            g["rows"].append((meta["orig"], name, span, [tuple(w) for w in ws] if ws else None,
                              (meta.get("track") or "") if len(tracks) > 1 else "", color, fixed, seg.get("layer")))
        for g in groups.values():
            g.pop("words", None)
            g["rows"].sort(key=lambda r: r[2][0])
        return list(groups.values())

    def timed_lines(self, plan: list) -> list:
        """The lines (captions.Line) with their times lined up with the voice in their audio
        (can be slow on long files: run it off the window's thread)."""
        import os
        from transcriber.captions import Line
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
            for (text, name, s0, words, track, color, fixed, layer), (st, en) in zip(g["rows"], spans):
                # Whisper's first word often starts early: the words stay inside the lined-up time.
                if fixed:                              # (set by hand on the timeline: as it is)
                    st, en = s0
                out.append(Line(st, en, text, name, words, track, color, fixed,
                                int(layer) if layer is not None else None))
        return sorted(out, key=lambda ln: ln.start)

    def caption_cues(self, plan: list, video: Optional[str] = None, a: float = 0.0, b: Optional[float] = None) -> list:
        """Captions (captions.Cue) for the plan: lined up with the voice, cut into caption-sized
        pieces, and (for a video) moved onto nearby scene cuts between a and b."""
        from transcriber import captions as C
        lines = self.timed_lines(plan)
        if b is not None:
            lines = [ln for ln in lines if ln.end > a - 1 and ln.start < b + 1]
        if not C.OPTIONS.get("split", True):
            many = len({ln.name for ln in lines if ln.name}) > 1
            cues = [C.Cue(ln.start, max(ln.end, ln.start + C.MIN_DUR), [(f"{ln.name}: " if ln.name and many else "")
                                                                         + ln.text], ln.name) for ln in lines]
        else:
            cuts = None
            if video and C.OPTIONS.get("cuts", True) and lines:
                from transcriber.timing import scene_cuts
                lo = a if b is not None else lines[0].start
                hi = b if b is not None else lines[-1].end
                try:
                    cuts = scene_cuts(video, max(lo - 1, 0), hi + 1)
                except Exception:
                    cuts = None
            cues = C.build(lines, cuts=cuts)
        return C.between(cues, a, b) if b is not None else cues

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
    def clip_command(source, a, b, out, video, sources=None, srt=None, style="", track=None, language="") -> list:
        """The ffmpeg command for a clip from a to b seconds. sources: the sounds to mix
        ({"file": other recording or None for the source's own track "stream", "offset": where that
        recording starts on the source's timeline}); None = the source's sound as it is. srt: captions
        to burn in (an .ass file); track: an .srt to put inside as a subtitle track (times from 0)."""
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
        if track and video:                     # (every input comes before the output options)
            cmd += ["-sub_charenc", "UTF-8", "-i", track]
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
                fc.append(f"[0:v:0]subtitles={srt}" + (f":force_style='{style}'" if style else "") + "[vout]")
                vmap = "[vout]"
            if fc:
                cmd += ["-filter_complex", ";".join(fc)]
            cmd += ["-map", vmap, "-map", amap or "0:a:0?"]
            if track:
                from transcriber.subtitle_track import language_code, output_for
                lang = language_code(language)
                cmd += ["-map", f"{n_in}:0", "-c:s", output_for(out)[1],
                        "-metadata:s:s:0", f"language={lang}", "-metadata:s:s:0", "title=Captions",
                        "-disposition:s:0", "default"]
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart"]
        else:
            if fc:
                cmd += ["-filter_complex", ";".join(fc), "-map", amap]
            cmd += ["-vn", "-c:a", "libmp3lame", "-q:a", "2"]
        cmd.append(out)
        return cmd

    def _export_clip(self, source, a, b, out, video, plan, sources=None, mode="burn", highlight=False):
        """mode: "track" (a subtitle track inside the clip), "burn" (drawn on the picture), "both", "off".
        highlight: the burned-in captions color each word as it's said."""
        self.status_var.set(f"Exporting the clip ({_fmt(a)} to {_fmt(b)})...")
        self._busy(True, "Exporting clip")

        def work():
            tmp = tempfile.mkdtemp(prefix="wt_clip_")
            srt = track = None
            caps = self.caption_cues(plan, source if video else None, a, b) if plan else None
            if video and caps and mode in ("track", "both"):
                from transcriber.captions import to_srt
                Path(tmp, "clip.srt").write_text(to_srt(caps, offset=a), encoding="utf-8")
                track = "clip.srt"
            if video and caps and mode in ("burn", "both"):
                # Burned in with the look from Settings > Captions (size, color, box, speaker colors).
                Path(tmp, "clip.ass").write_text(self._ass_text(caps, a, highlight), encoding="utf-8")
                srt = "clip.ass"
            style = ""
            cmd = self.clip_command(source, a, b, str(Path(out).resolve()), video, sources, srt, style, track,
                                    self._transcript_language())
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

    def _ass_text(self, caps: list, offset: float = 0.0, highlight: bool = False) -> str:
        """Captions as an .ass file with the look from Settings > Captions (size, color, box,
        speaker colors), for burning in. offset: the clip's start (its times count from 0).
        highlight: each word colored as it's said (the speaker's color, or yellow)."""
        from transcriber.captions import to_ass
        from transcriber.player import CAPTION_COLORS, DEFAULT_CAPTIONS, caption_scale
        st = {**DEFAULT_CAPTIONS, **(self.caption_style or {})}
        return to_ass(caps, font=CAPTION_FONT, size=caption_scale(st)[0],
                      color=CAPTION_COLORS.get(st["color"], "#ffffff"),
                      background=st["background"] if st["background"] in ("box", "solid", "outline", "shadow") else "box",
                      position=st["position"], speaker=st["speaker"], names=st["names"], offset=offset,
                      highlight="speaker" if highlight else None)

    # ---------------------------------------------------------------- a video cut from the transcript

    def _meta_bounds(self, meta, order: Optional[dict] = None) -> tuple:
        """(start, end) seconds of a line: its start to the next line's start on its track, or its
        last word (plus a little), like _row_bounds but for any line (folded ones too)."""
        start = float(meta.get("sec", 0))
        track = meta.get("track") or ""
        if order is None:
            order = {}
        if track not in order:
            order[track] = sorted(float(m.get("sec", 0)) for m in self._all_row_metas()
                                  if (m.get("track") or "") == track)
        import bisect
        secs = order[track]
        k = bisect.bisect_right(secs, start)
        end = secs[k] if k < len(secs) else None
        wf = meta["files"].get("words")
        lines = self._words_for(wf) if wf else None
        if lines and meta["i"] < len(lines) and lines[meta["i"]]:
            last = lines[meta["i"]][-1][1]
            end = max(last, start + 0.5) if end is None else min(end, last + 0.4)
        return start, (end if end is not None else start + 5.0)

    def _cut_spans(self, which: str) -> list:
        """The (start, end) spans to keep: "marked" (every marker: a stretch, or its line), or
        "deleted" (everything except the lines deleted and not yet saved)."""
        from transcriber.cuts import keep_from_drop, merge_spans
        from transcriber.markers_ui import read_markers
        order: dict = {}
        duration = float(getattr(self.player, "length", 0) or 0) or None
        if which == "marked":
            spans = []
            f = getattr(self, "_marker_file", None)
            metas = [m for m in self._all_row_metas() if not self._is_deleted(m)]
            for m in read_markers(f) if f else []:
                t = float(m.get("time", -1))
                if m.get("end"):
                    spans.append((t, float(m["end"])))
                    continue
                hit = next((x for x in metas if abs(float(x.get("sec", 0)) - t) <= 1.01
                            and (x.get("track") or "") == (m.get("track") or "")
                            and (not m.get("text") or m["text"].strip() == (x.get("orig") or "").strip())), None)
                if hit is not None:
                    spans.append(self._meta_bounds(hit, order))
                elif t >= 0:
                    spans.append((t, t + 5.0))
            return merge_spans(spans, 0.0, duration)
        drop = [self._meta_bounds(m, order) for m in self._all_row_metas() if self._is_deleted(m)]
        if not duration:
            ends = [b for _a, b in drop] + [float(m.get("sec", 0)) + 5 for m in self._all_row_metas()]
            duration = max(ends) if ends else 0.0
        return keep_from_drop(drop, duration) if drop else []

    def _cut_dialog(self):
        """Export a video cut from the transcript: only the marked parts, or without the deleted lines."""
        from transcriber.cuts import merge_spans, total
        item = self._shown_item()
        source = item.get("source") if item else None
        if not source or not os.path.exists(source):
            self.status_var.set("The original file is needed to cut a video, and it isn't here.")
            return
        video = Path(source).suffix.lower() not in AUDIO_EXTENSIONS and not getattr(self.player, "_is_audio_file", False)
        marked, deleted = self._cut_spans("marked"), self._cut_spans("deleted")
        n_del = sum(1 for m in self._all_row_metas() if self._is_deleted(m))
        if not marked and not n_del:
            self.status_var.set("Mark the parts to keep (M on a line, or mark a selection), or delete the lines "
                                "to leave out, then export the cut video.")
            return
        win = tk.Toplevel(self)
        win.title("Cut video")
        win.transient(self)
        win.resizable(False, False)
        f = ttk.Frame(win, padding=(22, 18))
        f.pack(fill="both", expand=True)
        head = ttk.Frame(f)
        head.pack(anchor="w", pady=(0, 10))
        ttk.Label(head, text="Cut " + ("video" if video else "audio"), font=ui_font(12, bold=True)).pack(side="left")
        info_icon(head, self, "A new file made of parts of the original, joined together, with the captions "
                              "moved to match. The original is never touched.").pack(side="left", padx=6)
        which = tk.StringVar(value="marked" if marked else "deleted")

        def label(kind):
            if kind == "marked":
                return f"Keep only the marked parts  ({len(marked)} part{'s' if len(marked) != 1 else ''}, {_fmt(total(marked))})"
            return f"Leave out the deleted lines  ({n_del} line{'s' if n_del != 1 else ''} out, {_fmt(total(deleted))} kept)"
        for kind, spans in (("marked", marked), ("deleted", deleted)):
            ttk.Radiobutton(f, text=label(kind), value=kind, variable=which,
                            state="normal" if spans else "disabled").pack(anchor="w")
        pr = ttk.Frame(f)
        pr.pack(anchor="w", pady=(10, 0))
        ttk.Label(pr, text="Keep").pack(side="left")
        pad_var = tk.StringVar(value="0.3")
        ttk.Spinbox(pr, from_=0.0, to=5.0, increment=0.1, textvariable=pad_var, width=5, justify="right").pack(side="left", padx=6)
        ttk.Label(pr, text="s around each part").pack(side="left")
        info_icon(pr, self, "A little room before and after each part, so words aren't clipped. Parts that "
                            "touch are joined.").pack(side="left", padx=4)
        caps_var = tk.StringVar(value=(self.caption_style or {}).get("clip_captions") or "both" if video else "off")
        cr = ttk.Frame(f)
        cr.pack(anchor="w", pady=(12, 0))
        hdr = ttk.Frame(cr)
        hdr.pack(anchor="w")
        ttk.Label(hdr, text="Captions").pack(side="left")
        info_icon(hdr, self, "Like Export clip: burned into the picture, a subtitle track inside the file "
                             "(VLC, TVs, YouTube; not Microsoft's Media Player), both, or none.").pack(side="left", padx=4)
        for value, text in (("both", "Burned in + subtitle track"), ("burn", "Burned in"),
                            ("track", "Subtitle track only"), ("off", "None")):
            ttk.Radiobutton(cr, text=text, value=value, variable=caps_var,
                            state="normal" if video else "disabled").pack(anchor="w", padx=(10, 0))
        hl_var = tk.BooleanVar(value=bool((self.caption_style or {}).get("clip_highlight")))
        ttk.Checkbutton(cr, text="Highlight each word as it's said", variable=hl_var,
                        state="normal" if video else "disabled").pack(anchor="w", pady=(6, 0))
        sound = self._clip_sound_options(item, source)
        sound_vars = []
        if sound:
            sb = ttk.Frame(f)
            sb.pack(anchor="w", pady=(12, 0))
            ttk.Label(sb, text="Sound").pack(anchor="w")
            for opt in sound:
                v = tk.BooleanVar(value=opt["on"])
                ttk.Checkbutton(sb, text=opt["label"], variable=v).pack(anchor="w", padx=(10, 0))
                sound_vars.append((opt, v))
        status = ttk.Label(f, text="", style="Muted.TLabel")
        status.pack(anchor="w", pady=(8, 0))
        buttons = ttk.Frame(f)
        buttons.pack(fill="x", pady=(14, 0))

        def go():
            try:
                pad = max(float(pad_var.get()), 0.0)
            except ValueError:
                status.configure(text="The room around each part should be a number of seconds.")
                return
            duration = float(getattr(self.player, "length", 0) or 0) or None
            spans = merge_spans(marked if which.get() == "marked" else deleted, pad, duration)
            if not spans:
                status.configure(text="Nothing to keep.")
                return
            picked = [o for o, v in sound_vars if v.get()]
            if sound_vars and not picked:
                status.configure(text="Pick at least one sound to include.")
                return
            ext = ".mp4" if video else ".mp3"
            base = item["key"]
            default = f"{base} ({'highlights' if which.get() == 'marked' else 'cut'}){ext}"
            folder = Path(self._current_version(item)["files"].get("txt") or source).parent
            out = filedialog.asksaveasfilename(parent=win, title="Save the cut " + ("video" if video else "audio"),
                                               initialdir=str(folder), initialfile=default, defaultextension=ext,
                                               filetypes=[(ext.lstrip(".").upper(), "*" + ext)])
            if not out:
                return
            if os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(source)):
                status.configure(text="Pick another name: the original is never replaced.")
                return
            mode = caps_var.get() if video else "off"
            plan = self._caption_plan() if mode != "off" else None
            if video:
                self.caption_style = {**(self.caption_style or {}), "clip_captions": mode,
                                      "clip_highlight": bool(hl_var.get())}
                from transcriber.ui_common import save_settings
                save_settings(self._settings())
            win.destroy()
            self._export_cut(source, spans, out, video, plan, picked or None, mode, bool(hl_var.get()))

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

    def _export_cut(self, source, spans, out, video, plan, sources=None, mode="both", highlight=False):
        """Each span exported like a clip (same encoding), the parts joined without re-encoding,
        and the captions put inside as a track with the gaps taken out of their times."""
        from transcriber import captions as C
        from transcriber.cuts import concat_command, concat_list, remap_cues, total
        n = len(spans)
        self.status_var.set(f"Cutting: {n} part{'s' if n != 1 else ''}, {_fmt(total(spans))} in all...")
        self._busy(True, "Cutting video")
        language = self._transcript_language()

        def say(msg):
            self.events.put(("call", lambda: self.status_var.set(msg)))

        def work():
            tmp = tempfile.mkdtemp(prefix="wt_cut_")
            err = ""
            try:
                cues = []
                if plan:
                    say("Lining the captions up with the voice...")
                    cues = C.build(self.timed_lines(plan))
                parts = []
                for k, (a, b) in enumerate(spans, 1):
                    say(f"Exporting part {k} of {n} ({_fmt(a)} to {_fmt(b)})...")
                    srt = None
                    if video and cues and mode in ("burn", "both"):
                        caps = C.between(cues, a, b)
                        if caps:
                            Path(tmp, f"part{k}.ass").write_text(self._ass_text(caps, a, highlight), encoding="utf-8")
                            srt = f"part{k}.ass"
                    part = str(Path(tmp, f"part{k}{'.mp4' if video else '.mp3'}"))
                    cmd = self.clip_command(source, a, b, part, video, sources, srt, "")
                    r = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp, creationflags=_NO_WINDOW)
                    if r.returncode:
                        tail = r.stderr.strip().splitlines()
                        err = (tail[-1] if tail else f"ffmpeg stopped (code {r.returncode})")
                        break
                    parts.append(part)
                if not err:
                    say("Joining the parts...")
                    Path(tmp, "parts.txt").write_text(concat_list(parts), encoding="utf-8")
                    track = video and cues and mode in ("track", "both")
                    joined = str(Path(tmp, "joined.mp4")) if track else str(Path(out).resolve())
                    cmd = concat_command(str(Path(tmp, "parts.txt")), joined, video)
                    r = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp, creationflags=_NO_WINDOW)
                    if r.returncode:
                        tail = r.stderr.strip().splitlines()
                        err = (tail[-1] if tail else f"ffmpeg stopped (code {r.returncode})")
                    elif track:
                        from transcriber.subtitle_track import mux_command
                        moved = remap_cues(cues, spans)
                        Path(tmp, "cut.srt").write_text(C.to_srt(moved), encoding="utf-8")
                        cmd = mux_command(joined, str(Path(tmp, "cut.srt")), str(Path(out).resolve()), language)
                        r = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp, creationflags=_NO_WINDOW)
                        if r.returncode:
                            tail = r.stderr.strip().splitlines()
                            err = (tail[-1] if tail else f"ffmpeg stopped (code {r.returncode})")
            except Exception as exc:
                err = str(exc)
            finally:
                try:
                    for p in Path(tmp).iterdir():
                        p.unlink()
                    os.rmdir(tmp)
                except OSError:
                    pass
            msg = (f"Couldn't cut the video: {err}" if err
                   else f"Saved {Path(out).name}: {n} part{'s' if n != 1 else ''}, {_fmt(total(spans))}.")
            self.events.put(("call", lambda: (self._busy(False), self.status_var.set(msg))))

        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- the video with a subtitle track

    def _transcript_language(self) -> str:
        """The language the shown transcript was made in ("" when unknown), for subtitle tracks."""
        for _k, files in getattr(self, "_stats_parts", []):
            if files.get("speakers"):
                from transcriber.speakers import read_speakers_file
                language = (read_speakers_file(files["speakers"]) or {}).get("language", "")
                if language:
                    return language
        return ""

    def _save_with_captions(self):
        """Save a copy of the shown transcript's video with the captions inside it as a subtitle
        track (players and TVs list it under Subtitles / CC). Nothing is re-encoded."""
        from transcriber import subtitle_track as st
        from transcriber.core import has_video_stream, media_duration
        if not self.display or self.display[0] != "saved":
            self.status_var.set("Pick a transcript first.")
            return
        item = self._shown_item() or {}
        source = getattr(self.player, "path", None) or item.get("source")
        if not source or not os.path.exists(source):
            self.status_var.set("Can't find the video this transcript came from.")
            return
        if Path(source).suffix.lower() in AUDIO_EXTENSIONS or not has_video_stream(source):
            self.status_var.set("This is an audio file: a subtitle track needs a video.")
            return
        if self._edits:
            if not self._confirm(title="Save changes?", heading="Save your changes first?", body="", items=[],
                                 detail="The video gets the saved transcript, so your unsaved edits are saved "
                                        "first.", confirm="Save and continue"):
                return
            if not self._edit_save():
                return
        ext, _codec = st.output_for(source)
        kind = {".mkv": "MKV video", ".mp4": "MP4 video", ".m4v": "M4V video", ".mov": "QuickTime video"}[ext]
        out = filedialog.asksaveasfilename(parent=self, title="Save video with captions",
                                           initialdir=str(Path(source).parent), initialfile=st.default_name(source),
                                           defaultextension=ext, filetypes=[(kind, "*" + ext)])
        if not out:
            return
        if os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(source)):
            self.status_var.set("Pick another name: the original video is never replaced.")
            return
        plan = self._caption_plan()
        language = self._transcript_language()
        self.status_var.set("Saving the video with captions (lining them up with the voice)...")
        self._busy(True, "Saving video with captions")

        def work():
            from transcriber.captions import to_srt
            err, n = "", 0
            tmp = tempfile.mkdtemp(prefix="wt_subs_")
            part = st.part_path(out)
            try:
                caps = self.caption_cues(plan, source)
                n = len(caps)
                if not caps:
                    err = "this transcript has no lines"
                else:
                    srt = os.path.join(tmp, "captions.srt")
                    Path(srt).write_text(to_srt(caps), encoding="utf-8")
                    duration = media_duration(source)

                    def progress(f):
                        self.events.put(("call", lambda: self.status_var.set(
                            f"Saving the video with captions... {int(f * 100)}%")))
                    err = st.run(st.mux_command(source, srt, part, language), duration, progress)
                    if not err:
                        os.replace(part, out)
            except Exception as exc:
                err = str(exc)
            finally:
                for f in Path(tmp).glob("*"):
                    try:
                        f.unlink()
                    except OSError:
                        pass
                try:
                    os.rmdir(tmp)
                except OSError:
                    pass
            self.events.put(("call", lambda: (self._busy(False), self._saved_with_captions(out, n, err))))

        threading.Thread(target=work, daemon=True).start()

    def _saved_with_captions(self, out, n, err):
        if err:
            self.status_var.set(f"Couldn't save the video with captions: {err}")
            return
        self.status_var.set(f"Saved {Path(out).name} with {n} captions on a subtitle track.")
        if self._confirm(title="Saved", heading="Video saved with captions",
                         body=str(Path(out).parent) + ":", items=[Path(out).name],
                         detail="The captions are a subtitle track: in VLC, on a TV or in Plex, turn them on or "
                                "off from the Subtitles / CC menu. They show by default.",
                         confirm="Show in folder", cancel="OK"):
            from transcriber.ui_common import show_in_folder
            show_in_folder(Path(out))

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
