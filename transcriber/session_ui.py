"""Combining recordings made at the same time (a lav mic per person, a camera, isolated game
captures) into one transcript: the Combine window, and transcribing a combined recording.

A mixin for the App class in app.py."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from transcriber.core import AUDIO_EXTENSIONS, Segment, has_video_stream, safe_label, transcribe_file
from transcriber.files import save_transcript
from transcriber.names import recording_folder
from transcriber.icons import info_icon
from transcriber.ui_common import PENDING, set_dark_titlebar
from transcriber.fonts import ui_font

HOW = {"sound": "matched by sound", "clock": "by recording time", "start": "assumed same start"}


class SessionMixin:

    # ---- queue right-click

    def _queue_menu(self, e):
        row = self.tree.identify_row(e.y)
        if row and row not in self.tree.selection():
            self.tree.selection_set(row)
        jobs = [j for j in (self._job(i) for i in self.tree.selection()) if j]
        if not jobs:
            return "break"
        p = self._palette()
        dark = self.current_theme == "dark"
        menu = tk.Menu(self, tearoff=0, background="#2b2b2b" if dark else "#ffffff", foreground=p["fg"],
                       activebackground=p["hover"], activeforeground=p["fg"], disabledforeground=p["muted"],
                       font=ui_font(10), relief="flat", borderwidth=1)
        can = len(jobs) >= 2 and all(j["status"] == PENDING and not j.get("session") for j in jobs)
        menu.add_command(label="Combine as one recording...", state="normal" if can else "disabled",
                         command=lambda: self._combine_dialog(jobs))
        if not can and len(jobs) < 2:
            menu.add_command(label="(select 2 or more files to combine)", state="disabled")
        menu.add_separator()
        menu.add_command(label="Remove", command=self._remove_selected)
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()
        return "break"

    # ---- the Combine window

    def _combine_dialog(self, jobs, existing=None, name=None, new_paths=None):
        """Combine queued files, or (existing=...) add new recordings to a saved transcript: its
        lines are reused as they are and only the new files get transcribed."""
        existing = existing or []
        adding = bool(existing)
        new = list(new_paths) if new_paths is not None else [j["path"] for j in jobs]
        paths = [e["path"] for e in existing] + new

        def is_video(p):
            return Path(p).suffix.lower() not in AUDIO_EXTENSIONS and has_video_stream(p)
        videos = [p for p in paths if os.path.exists(p) and is_video(p)]
        win = tk.Toplevel(self)
        win.title("Add recordings" if adding else "Combine recordings")
        win.transient(self)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=(22, 18))
        frame.pack(fill="both", expand=True)
        head = ttk.Frame(frame)
        head.grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 12))
        ttk.Label(head, text=(f"Add {len(new)} recording{'s' if len(new) != 1 else ''} to \u201c{name}\u201d"
                              if adding else f"Combine {len(paths)} recordings"),
                  font=ui_font(12, bold=True)).pack(side="left")
        info_icon(head, self, ("The transcript you have is kept as it is; only the new files are transcribed. "
                               "Everyone's lines are then shown together in time order, on the same timeline, "
                               "so markers stay where they are." if adding else
                               "For recordings made at the same time on separate devices, like a lav mic for each "
                               "person. Each file is one person; their lines are shown together in time order.")).pack(
            side="left", padx=6)

        ttk.Label(frame, text="Name").grid(row=1, column=0, sticky="w")
        stems = [Path(p).stem for p in paths]
        common = name or os.path.commonprefix(stems).strip(" -_.") or stems[0]
        name_var = tk.StringVar(value=common)
        ttk.Entry(frame, textvariable=name_var, width=30, state="disabled" if adding else "normal").grid(
            row=1, column=1, columnspan=3, sticky="w", pady=(0, 10))

        for c, text in ((1, "File"), (2, "Person"), (3, "Starts at")):
            ttk.Label(frame, text=text, style="Muted.TLabel").grid(row=2, column=c, sticky="w", padx=(0, 10))
        ttk.Label(frame, text="Use", style="Muted.TLabel").grid(row=2, column=0, sticky="w")
        names = sorted(p["name"] for p in self.people.people)
        rows = []
        for i, path in enumerate(paths):
            r = 3 + i
            ex = existing[i] if i < len(existing) else None
            vid = path in videos
            use = tk.BooleanVar(value=True if ex else not (vid and len(paths) > len(videos)))
            ttk.Checkbutton(frame, variable=use, state="disabled" if ex else "normal").grid(row=r, column=0, sticky="w")
            label = Path(path.replace("\\", "/")).name + ("   (video)" if vid else "")
            label = label if len(label) < 34 else label[:31] + "..."
            if ex:
                label += "   (have it)"
            ttk.Label(frame, text=label).grid(row=r, column=1, sticky="w", padx=(0, 10), pady=3)
            guess = (ex or {}).get("label") or next((n for n in names if n.lower() in Path(path).stem.lower()), "")
            person = tk.StringVar(value=guess or ("Camera" if vid else ""))
            ttk.Combobox(frame, textvariable=person, values=names, width=16).grid(row=r, column=2, sticky="w",
                                                                                padx=(0, 10))
            off = tk.StringVar(value=f"{ex['offset']:+.2f}" if ex else "...")
            ttk.Entry(frame, textvariable=off, width=8, justify="right",
                      state="disabled" if ex else "normal").grid(row=r, column=3, sticky="w")
            how = ttk.Label(frame, text="", style="Muted.TLabel")
            how.grid(row=r, column=4, sticky="w", padx=(6, 0))
            rows.append({"path": path, "use": use, "person": person, "off": off, "how": how, "video": vid, "ex": ex})

        opts = ttk.Frame(frame)
        opts.grid(row=4 + len(paths), column=0, columnspan=5, sticky="w", pady=(12, 0))
        xtalk = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Remove crosstalk", variable=xtalk).pack(side="left")
        info_icon(opts, self, "Mics pick up the people nearby, so the same words get caught by several mics. "
                              "When two mics have the same words at the same moment, only the closest mic's line "
                              "is kept (compared with each mic's own usual level, so a mic set louder doesn't "
                              "win by volume). Lines only one mic has are always kept.").pack(side="left", padx=4)
        status = ttk.Label(frame, text="Lining up the recordings...", style="Muted.TLabel")
        status.grid(row=5 + len(paths), column=0, columnspan=5, sticky="w", pady=(8, 0))
        info_icon(frame, self, "Starts at = where each file begins, in seconds, compared with the first one. "
                               "Found by matching the sound the mics share, else the recording times saved in "
                               "the files. Type a number to fix it.").grid(row=2, column=4, sticky="w", padx=(6, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=6 + len(paths), column=0, columnspan=5, sticky="e", pady=(14, 0))
        go = ttk.Button(buttons, text="Add" if adding else "Combine", style="Accent.TButton", state="disabled")
        go.pack(side="right")
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 8))

        ref = 0 if adding else (paths.index(videos[0]) if videos else 0)

        def found(offsets):
            if not win.winfo_exists():
                return
            base = existing[0]["offset"] if adding else 0.0
            for row, (o, how) in zip(rows, offsets):
                if row["ex"]:
                    continue
                row["off"].set(f"{o + base:+.2f}")
                row["how"].configure(text=HOW.get(how, ""))
            status.configure(text="")
            go.configure(state="normal")

        def work():
            from transcriber.session import find_offsets
            try:
                offsets = find_offsets(paths, ref)
            except Exception:
                offsets = [(0.0, "start")] * len(paths)
            self.events.put(("call", lambda: found(offsets)))

        threading.Thread(target=work, daemon=True).start()

        def combine():
            files = []
            for row in rows:
                try:
                    offset = float(row["off"].get().replace("s", "").strip() or 0)
                except ValueError:
                    status.configure(text=f"\u201c{row['off'].get()}\u201d isn't a number of seconds.")
                    return
                f = {"path": row["path"], "label": row["person"].get().strip() or Path(row["path"]).stem,
                     "person_name": row["person"].get().strip(), "offset": offset,
                     "transcribe": row["use"].get(), "video": row["video"]}
                if row["ex"]:
                    f["existing_files"] = row["ex"].get("files")
                    f["transcribe"] = row["ex"].get("transcribed", True)
                files.append(f)
            if len({f["label"] for f in files}) < len(files):
                status.configure(text="Give each recording a different person.")
                return
            if not any(f["transcribe"] for f in files):
                status.configure(text="Tick at least one recording to transcribe.")
                return
            if adding:      # keep the transcript's timeline (and its markers) as it is
                video = existing[0].get("video")
                reference = existing[0].get("reference") or existing[0]["path"]
            else:
                video = videos[0] if videos else None
                reference = None
                # The transcript's timeline is the video's, or else the mic that starts first.
                zero = next((f["offset"] for f in files if f["path"] == video), None)
                if zero is None:
                    zero = min(f["offset"] for f in files)
                for f in files:
                    f["offset"] = round(f["offset"] - zero, 3)
            nm = name if adding else safe_label(name_var.get().strip() or common)
            for f in files:            # new names become People now, not from the transcribing thread
                if f["person_name"] and f["person_name"] != "Camera" and not self.people.by_name(f["person_name"]):
                    self.people.add(f["person_name"])
            win.destroy()
            self._add_session_job(jobs, {"name": nm, "files": files, "video": video, "crosstalk": xtalk.get(),
                                         "reference": reference})

        go.configure(command=combine)
        win.bind("<Escape>", lambda e: win.destroy())
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_reqheight()) // 3
        win.geometry(f"+{x}+{y}")
        set_dark_titlebar(win, self.current_theme == "dark")
        win.grab_set()
        return win

    # ---- adding recordings to a saved transcript

    def _saved_menu(self, e):
        row = self.saved.identify_row(e.y)
        if not row:
            return "break"
        if row not in self.saved.selection():
            self.saved.selection_set(row)
            self._on_saved_select()
        item = self.saved_items.get(row)
        if not item:
            return "break"
        p = self._palette()
        dark = self.current_theme == "dark"
        menu = tk.Menu(self, tearoff=0, background="#2b2b2b" if dark else "#ffffff", foreground=p["fg"],
                       activebackground=p["hover"], activeforeground=p["fg"], disabledforeground=p["muted"],
                       font=ui_font(10), relief="flat", borderwidth=1)
        menu.add_command(label="Add another recording...", command=lambda: self._add_recording(item))
        menu.add_command(label="Line up times with the voice", command=lambda: self._line_up_times(item))
        if item.get("session"):
            menu.add_command(label="Remove crosstalk (lines caught by another mic)",
                             command=lambda: self._remove_crosstalk(item))
        menu.add_command(label="Save video with captions...", command=self._save_with_captions)
        menu.add_command(label="Check captions...", command=self._check_captions)
        menu.add_command(label="Show in folder", command=self._show_saved_in_folder)
        menu.add_separator()
        menu.add_command(label="Delete", command=self._delete_saved)
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _remove_crosstalk(self, item):
        """A recording with a mic per person: each mic also catches the others, so the same words show
        up on several tracks. Mark the copies for removal (kept: the mic that was closest, for its own
        level). Nothing is saved until Save, so they can be looked over first (or Undo)."""
        import os
        parts = [p for p in self._existing_parts(item) if p.get("files") and p["files"].get("speakers")]
        if len(parts) < 2:
            self.status_var.set("Removing crosstalk needs a recording with a mic per person.")
            return
        iid = next((i for i, it in self.saved_items.items() if it.get("key") == item.get("key")), None)
        if iid:                                     # show it (every track) so the marks can be seen
            self.saved.selection_set(iid)
            self._list_mode = "library"
            self._on_saved_select()
        self._show_tab("transcript")
        self.status_var.set("Finding crosstalk (reading each mic's audio to tell who was closest)...")
        self._busy(True, "Finding crosstalk")

        def work():
            from transcriber.session import envelope, find_crosstalk, relative_levels
            from transcriber.speakers import read_speakers_file
            err, drop, missing = "", [], []
            try:
                tracks, envs = [], []
                for p in parts:
                    data = read_speakers_file(p["files"]["speakers"]) or {}
                    tracks.append([(float(s["start"]), float(s["end"]), s.get("text", ""))
                                   for s in data.get("segments", [])])
                    env = envelope(p["path"]) if p["path"] and os.path.exists(p["path"]) else None
                    if env is None or not len(env):
                        missing.append(p["label"] or os.path.basename(p["path"] or ""))
                        env = None
                    envs.append(env)
                if missing:
                    err = ("couldn't read the audio of " + ", ".join(missing) +
                           " (is the drive connected?), so it can't tell who was closest")
                else:
                    drop = find_crosstalk(tracks, relative_levels(envs, [p["offset"] for p in parts]))
            except Exception as exc:
                err = str(exc) or type(exc).__name__
            self.events.put(("call", lambda: self._crosstalk_found(parts, drop, err)))
        threading.Thread(target=work, daemon=True).start()

    def _crosstalk_found(self, parts, drop, err):
        self._busy(False)
        if err:
            self.status_var.set(f"Couldn't remove crosstalk: {err}.")
            return
        known = {m["key"] for m in self._all_row_metas()}
        new, n, per = dict(self._edits), 0, []
        for p, d in zip(parts, drop):
            fkey = self._file_key(p["files"])
            marked = [k for k in sorted(d) if (fkey, k) in known and new.get((fkey, k), "") is not None]
            for k in marked:
                new[(fkey, k)] = None
            n += len(marked)
            if marked:
                per.append(f"{p['label']} {len(marked)}")
        if not n:
            self.status_var.set("No crosstalk found: no line was caught by a closer mic too.")
            return
        self._end_edit()
        self._push_state(new)
        self._apply_edit_state(new)
        self._rerender_in_place()
        self.status_var.set(f"{n} line{'s' if n != 1 else ''} marked for removal ({', '.join(per)}): the same "
                            "words, caught by another person's mic. Look them over, then Save (or Undo).")

    def _existing_parts(self, item) -> list:
        """What a saved transcript already has, as rows for the Add window:
        [{"path", "label", "offset", "files", "transcribed", "video", "reference"}]."""
        from transcriber.session import read_session, reference_file
        if item.get("session"):
            data = read_session(item["session"]) or {}
            ref = reference_file(data)
            parts = []
            for f in data.get("files") or []:
                versions = item["tracks"].get(f["label"]) or []
                parts.append({"path": f["path"], "label": f["label"], "offset": float(f.get("offset", 0)),
                              "files": versions[-1]["files"] if versions else None,
                              "transcribed": bool(versions) and f.get("transcribed", True),
                              "video": data.get("video"), "reference": ref})
            parts.sort(key=lambda x: x["path"] != ref)      # the timeline's file first
            return parts
        source = item.get("source")
        if not source:
            return []
        v = self._current_version(item)
        track = self._track_key(item)
        if track == self.TRACKS or v.get("parts"):
            v = (item["tracks"].get("") or next(iter(item["tracks"].values())))[-1]
        video = source if (os.path.exists(source) and Path(source).suffix.lower() not in AUDIO_EXTENSIONS
                           and has_video_stream(source)) else None
        return [{"path": source, "label": "", "offset": 0.0, "files": v["files"], "transcribed": True,
                 "video": video, "reference": source}]

    def _add_recording(self, item):
        from tkinter import filedialog
        from transcriber.core import MEDIA_EXTENSIONS
        existing = self._existing_parts(item)
        if not existing:
            self._confirm(title="Add recordings", heading="Can't add recordings to this one", body="", items=[],
                          detail="This transcript doesn't say which file it came from.", confirm="OK", cancel=None)
            return
        if not os.path.exists(existing[0]["path"]):
            self._confirm(title="Add recordings", heading="The original recording is needed", body="", items=[],
                          detail=f"It's used to line the new files up by sound, and it isn't here anymore:\n"
                                 f"{existing[0]['path']}", confirm="OK", cancel=None)
            return
        exts = " ".join(f"*{e}" for e in sorted(MEDIA_EXTENSIONS))
        start = str(Path(existing[0]["path"]).parent.parent)
        paths = filedialog.askopenfilenames(parent=self, title=f"Recordings to add to \u201c{item['key']}\u201d",
                                            initialdir=start, filetypes=[("Video and audio", exts), ("All files", "*.*")])
        have = {e["path"] for e in existing}
        paths = [p for p in paths if p not in have]
        if paths:
            self._combine_dialog([], existing=existing, name=item["key"], new_paths=paths)

    def _add_session_job(self, jobs, spec):
        """Replace the chosen files in the queue with one combined recording."""
        index = min((self.tree.index(j["iid"]) for j in jobs), default="end")
        for j in jobs:
            if j in self.jobs:
                self.jobs.remove(j)
                self.tree.delete(j["iid"])
                self.thumb_images.pop(j["iid"], None)
        from transcriber.session import reference_file
        ref = reference_file({"video": spec["video"], "files": spec["files"], "reference": spec.get("reference")})
        iid = self.tree.insert("", index, text="", image=self.icons["pending"], values=(PENDING, "..."))
        job = {"path": ref, "status": PENDING, "iid": iid, "outputs": [], "duration": None, "icon": "pending",
               "log": [], "segs": [], "tracks": [], "tracks_known": True, "silence_checked": True,
               "choices": ["mix"], "session": spec}
        self.jobs.append(job)
        self._session_label(job)
        self.thumb_q.put(("queue", iid, ref))
        self.tree.selection_set(iid)
        self._list_mode = "queue"
        self._show_tab("queue")
        self.status_var.set(f"Combined into “{spec['name']}”. Click Transcribe when ready.")

    def _session_label(self, job):
        spec = job["session"]
        n = sum(1 for f in spec["files"] if f["transcribe"])
        new = sum(1 for f in spec["files"] if f["transcribe"] and not f.get("existing_files"))
        extra = f"  ({new} new)" if new < n else ""
        self.tree.item(job["iid"], text=f"  {spec['name']}   ·  {n} mic{'s' if n != 1 else ''} combined{extra}")

    # ---- transcribing a combined recording

    def _run_session(self, job, n, total, settings, lang_code, out_dir):
        """Transcribe each person's file, line the lines up, drop crosstalk, save one transcript."""
        from transcriber.core import Transcript, media_duration
        from transcriber.session import drop_crosstalk, envelope, session_path, write_session
        spec = job["session"]
        use = [f for f in spec["files"] if f["transcribe"]]
        results = []
        for k, f in enumerate(use):
            label = f["label"]
            self.events.put(("track_begin", job, label, True))
            if f.get("existing_files"):               # already transcribed: reuse its lines as they are
                t = self._transcript_from(f["existing_files"], f["path"])
                self.events.put(("info", job, f"{label}: using the transcript you already have "
                                              f"({len(t.segments)} lines)."))
                results.append((f, t))
                continue
            duration = media_duration(f["path"]) or 0

            def on_segment(seg, k=k, f=f, duration=duration):
                pct = min(seg.end / duration, 1.0) if duration else 0
                overall = ((n - 1) + (k + pct) / len(use)) / total * 100
                self.events.put(("segment", job, Segment(seg.start + f["offset"], seg.end + f["offset"], seg.text),
                                 overall))

            t = transcribe_file(f["path"], settings["model"], lang_code or None, on_segment=on_segment,
                                should_cancel=self.cancel_flag.is_set,
                                log=lambda msg: self.events.put(("info", job, msg)),
                                track=None, track_name=label, prompt=self._vocab_prompt(f.get("person_name", "")))
            off = f["offset"]
            for s in t.segments:                       # onto the shared timeline
                s.start, s.end = max(s.start + off, 0.0), max(s.end + off, 0.0)
                s.words = [(max(a + off, 0.0), max(b + off, 0.0), w) for a, b, w in s.words]
            t.segments = [s for s in t.segments if s.end > 0]   # said before the timeline starts
            results.append((f, t))
        if spec.get("crosstalk") and len(results) > 1:
            self.events.put(("info", job, "Removing crosstalk..."))
            envs = [envelope(f["path"]) for f, _t in results]
            kept = drop_crosstalk([(t.segments, f["offset"], e) for (f, t), e in zip(results, envs)])
            for (f, t), segs in zip(results, kept):
                dropped = len(t.segments) - len(segs)
                t.segments = segs
                t.text = " ".join(s.text for s in segs)
                if dropped:
                    self.events.put(("info", job, f"{f['label']}: {dropped} line{'s' if dropped != 1 else ''} "
                                                  "picked up from someone else's mic removed."))
        outputs, parts = [], []
        for f, t in results:
            person = None
            if f["person_name"] and f["person_name"] != "Camera":
                person = self.people.by_name(f["person_name"])     # added when combining (main thread)
            if person and t.segments:                  # this mic is this person: name every line
                lab = f"PERSON_{person['id']}"
                t.speakers = [lab] * len(t.segments)
                t.people_ids, t.names, t.voiceprints = {lab: person["id"]}, {lab: person["name"]}, {}
            saved = save_transcript(Transcript(source=f["path"], model=t.model, language=t.language,
                                               segments=t.segments, text=t.text, track=f["label"],
                                               speakers=t.speakers, voiceprints=t.voiceprints,
                                               names=t.names, people_ids=t.people_ids),
                                    out_dir, settings["formats"], name=spec["name"])
            outputs += saved
            parts.append((f["label"], self._files_from(saved)))
        sp = session_path(recording_folder(out_dir, spec["name"]), spec["name"])
        write_session(sp, name=spec["name"], video=spec.get("video"), reference=spec.get("reference"),
                      files=[{"path": f["path"], "label": f["label"], "offset": f["offset"],
                              "transcribed": f["transcribe"]} for f in spec["files"]])
        outputs.append(sp)
        return outputs, parts

    def _transcript_from(self, files: dict, source: str):
        """A saved version as a Transcript: lines without names in front, word timings, and its
        speakers (so a person's mic keeps the names it had)."""
        from transcriber.core import Transcript
        from transcriber.files import read_header, read_version
        from transcriber.speakers import display_names
        segs, labels, data = read_version(files)
        head = read_header(files.get("txt")) if files.get("txt") else {}
        spk = (data or {}).get("speakers") or {}
        return Transcript(source=source, model=head.get("Model", (data or {}).get("model", "")),
                          language=head.get("Language", (data or {}).get("language", "")), segments=segs,
                          text=" ".join(x.text for x in segs), speakers=labels if len(labels) == len(segs) else [],
                          voiceprints={k: (v or {}).get("voiceprint") for k, v in spk.items()},
                          names=display_names(data, self.people) if data else {},
                          people_ids={k: (v or {}).get("person") for k, v in spk.items()})
