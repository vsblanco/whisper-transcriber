"""The Queue tab: adding files (including drag and drop), transcribing them one after another in
the background, and showing a file's live transcript.

A mixin for the App class in app.py."""

from __future__ import annotations

import os
import hashlib
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from transcriber.fonts import ui_font
from transcriber.ui_common import (
    APP_NAME,
    CANCELLED,
    DONE,
    DROP_ACCENT,
    FAILED,
    LANGUAGES,
    PENDING,
    WORKING,
    make_vignette,
    save_settings,
)
from transcriber.speakers import match_people
from transcriber.core import (
    AUDIO_EXTENSIONS,
    MEDIA_EXTENSIONS,
    audio_tracks,
    Segment,
    TranscriptionCancelled,
    extract_thumbnail,
    has_video_stream,
    media_duration,
    transcribe_file,
    silent_tracks,
)
from transcriber.files import save_transcript

from transcriber.optional import DND_FILES, HAS_DND


class QueueMixin:

    def _job_title(self, job) -> str:
        name = Path(job["path"]).name
        return f"{name}  (live)" if job["status"] == WORKING else name

    def _job_append(self, job, text, tag=None):
        job["log"].append((text, tag))
        if self.display == ("job", job["iid"]):
            self._insert(text, tag)

    def _show_job(self, job):
        self.display = ("job", job["iid"])
        self.player.load(job["path"], lambda job=job: job["segs"])
        entries = list(job["log"])
        parts = job.get("parts") or []
        choices = job.get("choices") or ["mix"]
        if job.get("session"):
            spec = job["session"]
            self.player.set_tracks([{"n": i, "name": f["label"], "transcribed": f["transcribe"], "file": f["path"],
                                     "offset": f["offset"]} for i, f in enumerate(spec["files"])
                                    if os.path.exists(f["path"])])
        elif job["status"] == DONE and parts and "mix" not in choices:
            done = {c: label for c, (label, _f) in zip(choices, parts)}
            self.player.set_tracks(self._player_tracks(job["path"], done))
        else:
            self.player.set_tracks(self._player_tracks(job["path"], None))
        if job["status"] == DONE and parts:
            named = [x for x in parts if x[0]]          # separate tracks win over the mixed one here
            entries, caps = self._merged_entries(named or parts[:1])
            entries += [e for e in job["log"] if e[1] == "info" and e[0].lstrip().startswith("Saved")]
            self.player.captions = lambda: caps
            self._set_call(job["path"], caps)
        if not entries:
            msg = {PENDING: "Waiting to be transcribed. Click Transcribe to start.",
                   CANCELLED: "Cancelled before it started."}.get(job["status"], "Starting...")
            entries = [(msg + "\n", "info")]
        self._render(self._job_title(job), entries)

    def _working_job(self):
        return next((j for j in self.jobs if j["status"] == WORKING), None)

    def _on_queue_select(self, _event=None):
        if self.tabs.index("current") != 0:
            return
        sel = self.tree.selection()
        job = self._job(sel[0]) if sel else None
        if job:
            self.follow_live = job["status"] == WORKING
            self._show_job(job)
        else:
            self.follow_live = True
            working = self._working_job()
            self._show_job(working) if working else self._show_general()

    def _add_paths(self, paths, allow_repeat: bool = False):
        added = 0
        for p in paths:
            p = os.path.normpath(p)
            if os.path.isdir(p):
                self._add_paths((str(c) for c in sorted(Path(p).iterdir())), allow_repeat)
                continue
            if Path(p).suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            if not allow_repeat and any(j["path"] == p and j["status"] in (PENDING, WORKING) for j in self.jobs):
                continue
            iid = self.tree.insert("", "end", text="  " + Path(p).name,
                                   image=self.icons["pending"], values=(PENDING, "..."))
            self.jobs.append({"path": p, "status": PENDING, "iid": iid, "outputs": [],
                              "duration": None, "icon": "pending", "log": [], "segs": [],
                              "tracks": [], "choices": None})
            self.thumb_q.put(("queue", iid, p))
            added += 1
        if added:
            self.status_var.set(f"{self._count(PENDING)} file(s) ready.")
            if self.tabs.index("current") != 0:
                self.tabs.select(0)

    def _add_files_dialog(self):
        exts = " ".join(f"*{e}" for e in sorted(MEDIA_EXTENSIONS))
        paths = filedialog.askopenfilenames(
            title="Choose videos or audio",
            filetypes=[("Video and audio", exts), ("All files", "*.*")],
        )
        self._add_paths(paths)

    def _make_drop_target(self, widget):
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<DropEnter>>", self._drag_enter)
        widget.dnd_bind("<<DropPosition>>", lambda e: e.action)
        widget.dnd_bind("<<DropLeave>>", self._drag_leave)
        widget.dnd_bind("<<Drop>>", self._on_drop)

    def _drag_enter(self, event):
        if self.tabs.index("current") != 0:
            self.tabs.select(0)
        self._show_drop_zone()
        return event.action

    def _drag_leave(self, event):
        # Showing the drop zone covers the list, which sends the list a "leave"; only the
        # drop zone itself leaving means the files were dragged away.
        if self.drop_canvas is not None and event.widget == self.drop_canvas:
            self._hide_drop_zone()
        return event.action

    def _show_drop_zone(self):
        q = self.queue_frame
        q.update_idletasks()
        w, h = max(q.winfo_width(), 50), max(q.winfo_height(), 50)
        if self.drop_canvas is None:
            self.drop_canvas = tk.Canvas(q, highlightthickness=0, borderwidth=0)
            if HAS_DND:
                self._make_drop_target(self.drop_canvas)
        c = self.drop_canvas
        theme = self.current_theme or "light"
        key = (w, h, theme)
        if getattr(self, "_vignette_key", None) != key:
            self._vignette = make_vignette(w, h, theme)
            self._vignette_key = key
        acc = DROP_ACCENT[theme]
        fg = "#1c1c1c" if theme == "light" else "#f2f2f2"
        muted = "#4a4a4a" if theme == "light" else "#b8b8b8"
        c.delete("all")
        c.configure(width=w, height=h)
        c.create_image(0, 0, image=self._vignette, anchor="nw")
        pad, r = 12, 14
        x0, y0, x1, y1 = pad, pad, w - pad, h - pad
        for coords in ((x0 + r, y0, x1 - r, y0), (x0 + r, y1, x1 - r, y1), (x0, y0 + r, x0, y1 - r), (x1, y0 + r, x1, y1 - r)):
            c.create_line(*coords, fill=acc, width=2, dash=(8, 6))
        for (ax, ay, start) in ((x0, y0, 90), (x1 - 2 * r, y0, 0), (x0, y1 - 2 * r, 180), (x1 - 2 * r, y1 - 2 * r, 270)):
            c.create_arc(ax, ay, ax + 2 * r, ay + 2 * r, start=start, extent=90, style="arc", outline=acc, width=2)
        cx, cy = w / 2, h / 2 - 18
        c.create_oval(cx - 26, cy - 26, cx + 26, cy + 26, outline=acc, width=2)
        c.create_line(cx, cy - 12, cx, cy + 12, fill=acc, width=3)
        c.create_line(cx - 10, cy + 2, cx, cy + 12, cx + 10, cy + 2, fill=acc, width=3)
        c.create_text(cx, cy + 46, text="Drop to add to the queue", fill=fg, font=ui_font(13, bold=True))
        c.create_text(cx, cy + 70, text="Videos, audio, or whole folders", fill=muted, font=ui_font(9))
        c.place(x=0, y=0, relwidth=1, relheight=1)
        tk.Misc.lift(c)  # Canvas.lift raises a drawing item, so call the widget version

    def _hide_drop_zone(self):
        if self.drop_canvas is not None:
            self.drop_canvas.place_forget()

    def _on_drop(self, event):
        self._hide_drop_zone()
        self._add_paths(self.tk.splitlist(event.data))
        return event.action

    def _remove_selected(self):
        for iid in self.tree.selection():
            job = self._job(iid)
            if job and job["status"] != WORKING:
                self.jobs.remove(job)
                self.tree.delete(iid)
                self.thumb_images.pop(iid, None)
        self._on_queue_select()

    def _clear_finished(self):
        for job in [j for j in self.jobs if j["status"] in (DONE, FAILED, CANCELLED)]:
            self.jobs.remove(job)
            self.tree.delete(job["iid"])
            self.thumb_images.pop(job["iid"], None)
        self._on_queue_select()

    def _job(self, iid):
        return next((j for j in self.jobs if j["iid"] == iid), None)

    def _count(self, status):
        return sum(1 for j in self.jobs if j["status"] == status)

    def _set_status(self, job, status):
        job["status"] = status
        vals = list(self.tree.item(job["iid"], "values"))
        vals[0] = status
        self.tree.item(job["iid"], values=vals)
        if self.display == ("job", job["iid"]):
            self.right.configure(text=self._job_title(job))

    def _start(self):
        if self.tabs.index("current") == 1 and self._selected_saved():
            self._transcribe_again(self._selected_saved())
            return
        pending = [j for j in self.jobs if j["status"] == PENDING]
        if not pending:
            messagebox.showinfo(APP_NAME, "Add at least one video or audio file first.")
            return
        for j in pending:                 # files added a moment ago may not be checked for tracks yet
            if not j.get("tracks_known"):
                try:
                    j["tracks"] = audio_tracks(j["path"])
                except Exception:
                    j["tracks"] = []
                j["tracks_known"] = True
                self._update_job_label(j)
        need = [j for j in pending if len(j["tracks"]) > 1 and not j.get("silence_checked") and not j.get("session")]
        if need:                          # check for silent tracks first, then come back here
            self.status_var.set("Checking audio tracks...")
            self.start_btn.configure(state="disabled")

            def check():
                for j in need:
                    j["silent"], j["silence_checked"] = silent_tracks(j["path"], len(j["tracks"])), True
                self.events.put(("start_again",))
            threading.Thread(target=check, daemon=True).start()
            return
        for j in pending:                 # only one track has sound: that's the whole recording
            if len(j["tracks"]) > 1 and len(self._audible(j)) <= 1 and not j.get("choices"):
                j["choices"] = ["mix"]
                self._update_job_label(j)
        ask = [j for j in pending if len(self._audible(j)) > 1 and not j.get("choices") and not j.get("session")]
        if ask:
            self._open_tracks_dialog(ask, on_done=self._start)
            return
        settings = self._settings()
        if not settings["formats"]:
            messagebox.showwarning(APP_NAME, "Pick at least one format to save in Settings (.txt, .srt or .vtt).")
            return
        save_settings(settings)
        if self.detect_speakers.get():
            from transcriber.speakers import usable_token
            tok = usable_token(self.hf_token.get())
            if tok is not None:
                settings["speaker_token"] = tok

        self.cancel_flag.clear()
        self.follow_live = True
        self.tabs.select(0)
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress.configure(value=0)
        lang_code = dict(LANGUAGES).get(settings["language"], "")
        self.worker = threading.Thread(
            target=self._run_queue, args=(pending, settings, lang_code), daemon=True
        )
        self.worker.start()

    def _transcribe_again(self, item):
        """Re-run the original file with the current settings, saved as a new version."""
        source = item.get("source")
        if not source or not os.path.exists(source):
            self._confirm(
                title="Original file not found",
                heading=f"Can't transcribe \u201c{item['key']}\u201d again",
                body="",
                items=[],
                detail=(f"The original file isn't at this location anymore:\n{source}" if source else
                        "This transcript doesn't record which file it came from."),
                confirm="OK", cancel=None)
            return
        fmts = [n for f, n in (("txt", "Text"), ("srt", "SRT"), ("vtt", "VTT")) if self.fmt_vars[f].get()]
        key = self._track_key(item)
        src_tracks = audio_tracks(source)
        if key == self.TRACKS:
            v = self._current_version(item)
            wanted = list(v["parts"]) if v else self._named_tracks(item)
        else:
            wanted = [key]
        choices = []
        for k in wanted:
            if k == "":
                choices.append("mix")
            else:
                idx = self._source_track_index(source, k)
                if idx is not None:
                    choices.append(idx)
        choices = choices or ["mix"]
        what = ", ".join("All tracks" if c == "mix" else self.track_name(src_tracks[c]) for c in choices)
        n = len(self._versions_for(item))
        others = self._count(PENDING)
        detail = f"Your {n} existing version{'s' if n != 1 else ''} will be kept. The new one becomes the latest."
        if others:
            detail += f" The {others} other file{'s' if others != 1 else ''} waiting in the queue will run too."
        if not self._confirm(
            title="Transcribe again?",
            heading=f"Transcribe \u201c{item['key']}\u201d again?",
            body="A new version will be made with your current settings:",
            items=([f"Track: {what}"] if len(item["tracks"]) > 1 or choices != ["mix"] else []) +
                  [f"Model: {self.model_var.get()}", f"Language: {self.lang_var.get()}",
                   f"Save as: {', '.join(fmts) or 'nothing selected'}"],
            detail=detail,
            confirm="Transcribe again",
        ):
            return
        for k in wanted:
            self.saved_version.pop(f"{item['key']}|{k}", None)    # show the new version when it's done
        before = len(self.jobs)
        self._add_paths([source], allow_repeat=True)
        if len(self.jobs) > before:
            job = self.jobs[-1]
            job["tracks"], job["choices"] = src_tracks, choices
            self._update_job_label(job)
            self.tree.selection_set(job["iid"])
        self.tabs.select(0)
        self._start()

    def _cancel(self):
        self.cancel_flag.set()
        self.status_var.set("Cancelling after the current line...")
        self.cancel_btn.configure(state="disabled")

    def _thumb_worker(self):
        """Reads each new file's length and grabs a preview frame."""
        while True:
            tree, iid, path = self.thumb_q.get()
            dur = media_duration(path) if tree == "queue" else None
            if tree == "queue":
                self.events.put(("meta", iid, dur))
                tracks = audio_tracks(path)
                self.events.put(("tracks", iid, tracks))
                if len(tracks) > 1:   # find silent tracks in the background (one quick pass over the audio)
                    threading.Thread(target=lambda iid=iid, path=path, n=len(tracks): self.events.put(
                        ("silent", iid, silent_tracks(path, n))), daemon=True).start()
            if Path(path).suffix.lower() in AUDIO_EXTENSIONS:
                self.events.put(("thumb", tree, iid, "audio"))
                continue
            try:
                st = os.stat(path)
                key = hashlib.sha1(f"{path}|{st.st_mtime}|{st.st_size}|{self.thumb_w}".encode()).hexdigest()
                self.thumb_dir.mkdir(parents=True, exist_ok=True)
                png = self.thumb_dir / f"{key}.png"
                if png.exists() or extract_thumbnail(path, str(png), self.thumb_w, self.thumb_h, dur):
                    self.events.put(("thumb", tree, iid, str(png)))
                else:
                    self.events.put(("thumb", tree, iid, "video" if has_video_stream(path) else "audio"))
            except OSError:
                self.events.put(("thumb", tree, iid, "doc" if tree == "saved" else "video"))

    def _run_queue(self, jobs, settings, lang_code):
        out_dir = Path(settings["output_dir"])
        total = len(jobs)
        for n, job in enumerate(jobs, 1):
            if self.cancel_flag.is_set():
                self.events.put(("status", job, CANCELLED))
                continue
            self.events.put(("begin", job, n, total))
            started = time.time()
            if job.get("session"):             # several recordings made at the same time
                try:
                    outputs, parts = self._run_session(job, n, total, settings, lang_code, out_dir)
                    self.events.put(("done", job, outputs, time.time() - started, n, total, parts))
                except TranscriptionCancelled:
                    self.events.put(("status", job, CANCELLED))
                    self.events.put(("info", job, "Cancelled."))
                except Exception as exc:
                    self.events.put(("error", job, str(exc)))
                continue
            duration = job.get("duration") or media_duration(job["path"]) or 0
            tracks = job.get("tracks") or []
            choices = [c for c in (job.get("choices") or ["mix"]) if c == "mix" or c < len(tracks)] or ["mix"]
            outputs, parts = [], []
            try:
                for ci, choice in enumerate(choices):
                    label = "All tracks" if choice == "mix" else self.track_name(tracks[choice])
                    self.events.put(("track_begin", job, label, len(choices) > 1))

                    def on_segment(seg: Segment, job=job, n=n, ci=ci):
                        pct = min(seg.end / duration, 1.0) if duration else 0
                        overall = ((n - 1) + (ci + pct) / len(choices)) / total * 100
                        self.events.put(("segment", job, seg, overall))

                    t = transcribe_file(
                        job["path"], settings["model"], lang_code or None,
                        on_segment=on_segment,
                        should_cancel=self.cancel_flag.is_set,
                        log=lambda msg, job=job: self.events.put(("info", job, msg)),
                        track=(0 if settings.get("track1_is_mix") and len(tracks) > 1 else None)
                        if choice == "mix" else choice,
                        track_name="" if choice == "mix" else label,
                        speaker_token=settings.get("speaker_token"),
                        prompt=self._vocab_prompt(),
                    )
                    if t.speakers:
                        t.people_ids, t.names = match_people(t.speakers, t.voiceprints, self.people)
                    saved = save_transcript(t, out_dir, settings["formats"])
                    outputs += saved
                    parts.append(("" if choice == "mix" else label, self._files_from(saved)))
                self.events.put(("done", job, outputs, time.time() - started, n, total, parts))
            except TranscriptionCancelled:
                self.events.put(("status", job, CANCELLED))
                self.events.put(("info", job, "Cancelled."))
            except Exception as exc:  # show any failure in the UI instead of crashing
                self.events.put(("error", job, str(exc)))
        self.events.put(("finished",))

    def _set_item_image(self, tree_name, iid, result):
        tree, record = (self.tree, self._job(iid)) if tree_name == "queue" else (self.saved, self.saved_items.get(iid))
        if not record or not tree.exists(iid):
            return
        if result in ("audio", "video", "doc"):
            record["icon"] = result
            tree.item(iid, image=self.icons[result])
            return
        try:
            img = tk.PhotoImage(file=result)
        except tk.TclError:
            record["icon"] = "video"
            tree.item(iid, image=self.icons["video"])
            return
        self.thumb_images[iid] = img
        record["icon"] = None
        tree.item(iid, image=img)
