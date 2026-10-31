"""Record from the microphone and/or PC audio (what's playing: a stream, a call) to transcribe it,
for when there's no file to download.

Each source is its own track in one .m4a saved in the transcripts folder (Recordings), like an OBS
recording, so your side and the other side of a call are transcribed separately and labeled. The
whole recording is transcribed when you stop (more accurate than bits as it goes, and speaker
detection needs all of it), and it stays the transcript's source: playback, the timeline, captions,
redoing with another model all work as with any file.

A mixin for the App class in app.py."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from transcriber.fonts import ui_font
from transcriber.icons import info_icon
from transcriber.ui_common import save_settings, set_dark_titlebar

SOURCES = (("mic", "Microphone"), ("pc", "PC audio (what you hear)"), ("app", "One app"))
HEARD_DB = -60.0             # a track quieter than this the whole time had nothing on it


class RecordMixin:

    def _record_prefs(self) -> dict:
        prefs = getattr(self, "record_prefs", None) or {}
        return {"mic": prefs.get("mic", True), "pc": prefs.get("pc", True), "mic_dev": prefs.get("mic_dev", ""),
                "pc_dev": prefs.get("pc_dev", ""), "transcribe": prefs.get("transcribe", True),
                "app": prefs.get("app", False), "app_exe": prefs.get("app_exe", ""),
                "app_title": prefs.get("app_title", "")}

    def _open_recorder(self):
        win = getattr(self, "_rec_win", None)
        if win is not None and win.winfo_exists():
            win.deiconify()
            win.lift()
            return
        from transcriber import recorder
        ok, msg = recorder.available()
        if not ok:
            self.status_var.set(msg)
            self._open_setup("soundcard")
            return
        try:
            devices = recorder.sources()
        except Exception as exc:
            self.status_var.set(f"Couldn't list the audio devices: {exc}")
            return
        import sys
        from transcriber import app_audio
        app_ok, app_why = app_audio.available()
        wins = app_audio.windows() if app_ok else []
        devices["app"] = [(w, app_audio.label(w)) for w in wins]
        prefs = self._record_prefs()
        win = tk.Toplevel(self)
        self._rec_win = win
        win.title("Record")
        win.resizable(False, False)
        win.attributes("-topmost", True)          # stays in view while you play the video elsewhere
        f = ttk.Frame(win, padding=(18, 14, 18, 14))
        f.pack(fill="both", expand=True)
        head = ttk.Frame(f)
        head.pack(fill="x")
        ttk.Label(head, text="Record to transcribe", font=ui_font(12, bold=True)).pack(side="left")
        info_icon(head, self, "Records what you pick, then transcribes it when you stop. With both, each is its "
                              "own track, so your voice and the other side (a call, a video) are transcribed "
                              "separately. The recording is kept in your transcripts folder (Recordings), so "
                              "it plays back like any file.").pack(side="left", padx=6)
        rows, state = {}, {"recs": [], "go": None, "t0": None, "tmp": None, "busy": False}
        grid = ttk.Frame(f)
        grid.pack(fill="x", pady=(10, 4))
        for r, (kind, label) in enumerate(SOURCES):
            if kind == "app" and sys.platform != "win32":
                continue                                    # (Windows only)
            on = tk.BooleanVar(value=bool(prefs[kind]) and (kind != "app" or app_ok))
            names = [n for _id, n in devices[kind]]
            ids = [i for i, _n in devices[kind]]
            if kind == "app":
                pick = app_audio.find(ids, prefs["app_exe"], prefs["app_title"])
                first = app_audio.label(pick) if pick else ""
            else:
                first = next((n for i, n in devices[kind] if i == prefs[f"{kind}_dev"]), "")
            dev = tk.StringVar(value=first or (names[0] if names else ""))
            cb = ttk.Checkbutton(grid, text=label, variable=on)
            cb.grid(row=r * 2, column=0, sticky="w", pady=(4, 0))
            holder = ttk.Frame(grid)
            holder.grid(row=r * 2, column=1, sticky="ew", padx=(10, 0), pady=(4, 0))
            box = ttk.Combobox(holder, textvariable=dev, values=names, state="readonly", width=34 if kind != "app" else 31)
            box.pack(side="left", fill="x", expand=True)
            meter = ttk.Progressbar(grid, maximum=1.0, length=120)
            meter.grid(row=r * 2 + 1, column=1, sticky="ew", padx=(10, 0), pady=(2, 4))
            rows[kind] = {"on": on, "dev": dev, "ids": ids, "names": names, "box": box, "check": cb, "meter": meter}
            if kind == "app":
                refresh = ttk.Button(holder, text="\u21bb", width=3, style="Tiny.TButton",
                                     command=lambda: refresh_apps())
                refresh.pack(side="left", padx=(4, 0))
                rows[kind]["refresh"] = refresh
                if not app_ok:
                    cb.state(["disabled"])
                    box.state(["disabled"])
                    refresh.state(["disabled"])
                    ttk.Button(holder, text="Install...", command=lambda: self._open_setup("appaudio")).pack(
                        side="left", padx=(4, 0))
                    ttk.Label(grid, text=app_why, style="Muted.TLabel", wraplength=300).grid(
                        row=r * 2 + 1, column=1, sticky="w", padx=(10, 0))
                    meter.grid_remove()
                info_icon(holder, self, "Records just this app's sound (a browser, Discord, a game), not "
                                        "everything the PC plays. A browser is recorded as a whole (all its tabs). "
                                        "Windows 10 version 2004 or newer.").pack(side="left", padx=(4, 0))
        grid.columnconfigure(1, weight=1)

        def refresh_apps():
            fresh = app_audio.windows()
            row = rows["app"]
            current = row["ids"][row["names"].index(row["dev"].get())] if row["dev"].get() in row["names"] else None
            row["ids"], row["names"] = fresh, [app_audio.label(w) for w in fresh]
            row["box"].configure(values=row["names"])
            keep = app_audio.find(fresh, current["exe"], current["title"]) if current else None
            row["dev"].set(app_audio.label(keep) if keep else (row["names"][0] if row["names"] else ""))
        transcribe = tk.BooleanVar(value=bool(prefs["transcribe"]))
        ttk.Checkbutton(f, text="Transcribe when I stop", variable=transcribe).pack(anchor="w", pady=(6, 0))
        clock = ttk.Label(f, text="0:00", font=ui_font(20, bold=True))
        clock.pack(pady=(8, 2))
        note = ttk.Label(f, text="", style="Muted.TLabel", wraplength=360, justify="center")
        note.pack()
        bt = ttk.Frame(f)
        bt.pack(fill="x", pady=(10, 0))
        start_btn = ttk.Button(bt, text="●  Record", style="Accent.TButton")
        start_btn.pack(side="right")
        discard_btn = ttk.Button(bt, text="Discard")
        close_btn = ttk.Button(bt, text="Close", command=lambda: on_close())
        close_btn.pack(side="right", padx=(0, 8))

        def ask(**kw) -> bool:
            """A question, shown above this always-on-top window."""
            win.attributes("-topmost", False)
            try:
                return self._confirm(**kw)
            finally:
                if win.winfo_exists():
                    win.attributes("-topmost", True)

        def remember():
            self.record_prefs = {"mic": rows["mic"]["on"].get(), "pc": rows["pc"]["on"].get(),
                                 "transcribe": transcribe.get(),
                                 **{f"{k}_dev": _id_for(rows[k]) for k in ("mic", "pc")}}
            if "app" in rows:
                w = _id_for(rows["app"])
                self.record_prefs.update(app=rows["app"]["on"].get(), app_exe=w["exe"] if w else "",
                                         app_title=w["title"] if w else "")
            save_settings(self._settings())

        def _id_for(row):
            name = row["dev"].get()
            return row["ids"][row["names"].index(name)] if name in row["names"] else ""

        def set_controls(recording: bool):
            for kind, row in rows.items():
                if kind == "app" and not app_ok:
                    continue
                for w in (row["check"], row["box"]) + ((row["refresh"],) if "refresh" in row else ()):
                    w.state(["disabled"] if recording else ["!disabled"])
                if not recording:
                    row["box"].state(["readonly"])
            if recording:
                start_btn.configure(text="■  Stop", command=stop)
                discard_btn.pack(side="left")
                close_btn.pack_forget()
            else:
                start_btn.configure(text="●  Record", command=start)
                discard_btn.pack_forget()
                close_btn.pack(side="right", padx=(0, 8))

        def start():
            from transcriber.recorder import AppRecorder, FileRecorder
            kinds = [k for k in rows if rows[k]["on"].get()]
            if not kinds:
                note.configure(text="Pick what to record: the microphone, PC audio or an app.")
                return
            if "app" in kinds and not _id_for(rows["app"]):
                note.configure(text="Pick the app to record (open it first, then \u21bb).")
                return
            remember()
            state["tmp"] = tempfile.mkdtemp(prefix="wt_rec_")
            state["go"] = threading.Event()
            state["recs"] = []
            for k in kinds:
                path = os.path.join(state["tmp"], f"{k}.wav")
                if k == "app":
                    w = _id_for(rows["app"])
                    rec = AppRecorder(w["pid"], w["name"], path, state["go"])
                else:
                    rec = FileRecorder(k, _id_for(rows[k]) or None, path, state["go"])
                rec.start()
                state["recs"].append(rec)
            note.configure(text="Starting...")
            start_btn.state(["disabled"])
            wait_ready(0)

        def wait_ready(n):
            if not win.winfo_exists():
                return
            if not all(r.ready.is_set() for r in state["recs"]) and n < 50:
                win.after(100, lambda: wait_ready(n + 1))
                return
            failed = [r for r in state["recs"] if r.error]
            if failed:
                for r in state["recs"]:
                    r.stop()
                shutil.rmtree(state["tmp"], ignore_errors=True)
                start_btn.state(["!disabled"])
                note.configure(text=f"Couldn't record from {failed[0].track_name}: {failed[0].error}")
                return
            state["go"].set()
            state["t0"] = time.monotonic()
            start_btn.state(["!disabled"])
            set_controls(True)
            note.configure(text="Recording. Play the video or call now; press Stop when it's done.")
            tick()

        def tick():
            if not win.winfo_exists() or state["t0"] is None:
                return
            secs = time.monotonic() - state["t0"]
            text = (f"{int(secs // 3600)}:{int(secs % 3600 // 60):02d}:{int(secs % 60):02d}" if secs >= 3600 else
                    f"{int(secs // 60)}:{int(secs % 60):02d}")
            clock.configure(text=text)
            for r in state["recs"]:
                rows[r.kind]["meter"].configure(value=r.level())
            self.status_var.set(f"● Recording {text}")
            err = next((r for r in state["recs"] if r.error), None)
            if err:
                note.configure(text=f"{err.track_name} stopped: {err.error}")
            win.after(100, tick)

        def finish_recording():
            state["t0"] = None
            for r in state["recs"]:
                r.stop()
            for row in rows.values():
                row["meter"].configure(value=0)
            set_controls(False)

        def discard():
            if not ask(title="Discard?", heading="Throw this recording away?", body="", items=[],
                                 detail="It won't be saved or transcribed.", confirm="Discard"):
                return
            finish_recording()
            shutil.rmtree(state["tmp"], ignore_errors=True)
            clock.configure(text="0:00")
            note.configure(text="Discarded.")
            self.status_var.set("Recording discarded.")
        discard_btn.configure(command=discard)

        def stop():
            length = time.monotonic() - state["t0"] if state["t0"] else 0
            finish_recording()
            heard = [(r.path, r.track_name) for r in state["recs"] if r.peak_db > HEARD_DB and not
                     (r.error and not os.path.exists(r.path))]
            quiet = [r.track_name for r in state["recs"] if r.peak_db <= HEARD_DB]
            if not heard:
                shutil.rmtree(state["tmp"], ignore_errors=True)
                note.configure(text="Nothing was heard, so nothing was saved. Check the devices above (and that "
                                    "the sound isn't muted), then try again.")
                self.status_var.set("Nothing was heard in the recording.")
                return
            folder = Path(self.out_var.get()) / "Recordings"
            dest = folder / time.strftime("Recording %Y-%m-%d %H-%M-%S.m4a")
            note.configure(text="Saving the recording..." + (f" ({', '.join(quiet)} had no sound, so it's left out.)"
                                                             if quiet else ""))
            start_btn.state(["disabled"])
            state["busy"] = True
            self._busy(True, "Saving the recording")
            tmp, want_text = state["tmp"], transcribe.get()

            def work():
                from transcriber.recorder import save_recording
                err = ""
                try:
                    folder.mkdir(parents=True, exist_ok=True)
                    save_recording(heard, str(dest))
                except Exception as exc:
                    err = str(exc) or type(exc).__name__
                if not err:
                    shutil.rmtree(tmp, ignore_errors=True)
                self.events.put(("call", lambda: saved(err, tmp, want_text, length)))
            threading.Thread(target=work, daemon=True).start()

            def saved(err, tmp, want_text, length):
                state["busy"] = False
                self._busy(False)
                if win.winfo_exists():
                    start_btn.state(["!disabled"])
                if err:
                    msg = f"Couldn't save the recording: {err}. The raw audio is still in {tmp}"
                    self.status_var.set(msg)
                    if win.winfo_exists():
                        note.configure(text=msg)
                    return
                if win.winfo_exists():
                    note.configure(text=f"Saved {dest.name}." + (" Transcribing it now." if want_text else ""))
                self._recorded(str(dest), [name for _p, name in heard], want_text)

        def on_close():
            if state["busy"]:
                return
            if state["t0"] is not None:
                if not ask(title="Stop recording?", heading="Stop recording?", body="", items=[],
                                     detail="The recording so far is saved" + (" and transcribed." if transcribe.get()
                                                                               else "."),
                                     confirm="Stop and save", cancel="Keep recording"):
                    return
                stop()
                if state["busy"]:
                    win.withdraw()                      # (closes for real once it's saved)
                    self._rec_close_after_save = True
                    return
            remember()
            win.destroy()

        start_btn.configure(command=start)
        win.protocol("WM_DELETE_WINDOW", on_close)
        win.bind("<Escape>", lambda e: on_close())
        win.update_idletasks()
        x = self.winfo_rootx() + self.winfo_width() - win.winfo_reqwidth() - 40
        y = self.winfo_rooty() + 80
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        set_dark_titlebar(win, self.current_theme == "dark")

    def _recorded(self, path: str, names: list, transcribe: bool):
        """A recording was saved: put it in the queue with its tracks, and transcribe it."""
        if getattr(self, "_rec_close_after_save", False):
            self._rec_close_after_save = False
            win = getattr(self, "_rec_win", None)
            if win is not None and win.winfo_exists():
                win.destroy()
        self._add_paths([path], allow_repeat=True)
        job = next((j for j in reversed(self.jobs) if j["path"] == os.path.normpath(path)), None)
        if job is None:
            return
        job.update(tracks=[{"n": i, "name": n, "original": n, "channels": 1} for i, n in enumerate(names)],
                   tracks_known=True, silence_checked=True, silent=set(),
                   choices=["mix"] if len(names) == 1 else list(range(len(names))))
        self._update_job_label(job)
        self.tree.selection_set(job["iid"])
        if not transcribe:
            self.status_var.set(f"Saved {Path(path).name}. It's in the queue; click Transcribe when ready.")
        elif self.worker and self.worker.is_alive():
            self._start_when_idle = True
            self.status_var.set(f"Saved {Path(path).name}. It's transcribed as soon as the current one finishes.")
        else:
            self._start()
