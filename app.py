"""Whisper Transcriber desktop app.

Drop in video or audio files, pick a model, and get timestamped transcripts
saved to Documents\\Transcripts (or any folder you choose).

Run:  pythonw app.py      (or double-click "Transcriber.bat")
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from transcriber import __version__
from transcriber.core import (
    MEDIA_EXTENSIONS, MODELS, Segment, TranscriptionCancelled, check_ffmpeg,
    default_output_dir, format_timestamp, media_duration, save_transcript, transcribe_file,
)

# Drag and drop is optional: works if tkinterdnd2 is installed, otherwise use "Add files".
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    BaseTk = TkinterDnD.Tk
    HAS_DND = True
except ImportError:  # pragma: no cover
    BaseTk = tk.Tk
    HAS_DND = False

APP_NAME = "Whisper Transcriber"
SETTINGS_PATH = Path(os.environ.get("APPDATA", Path.home() / ".config")) / "WhisperTranscriber" / "settings.json"

LANGUAGES = [
    ("Auto-detect", ""), ("English", "en"), ("Spanish", "es"), ("Portuguese", "pt"),
    ("French", "fr"), ("Italian", "it"), ("German", "de"), ("Japanese", "ja"),
    ("Korean", "ko"), ("Chinese", "zh"), ("Russian", "ru"), ("Arabic", "ar"),
]
MODEL_NOTES = {
    "tiny": "fastest, least accurate",
    "base": "fast, rough",
    "small": "balanced",
    "medium": "accurate (recommended)",
    "large": "most accurate, slowest",
    "turbo": "near-large accuracy, much faster",
}

PENDING, WORKING, DONE, FAILED, CANCELLED = "Waiting", "Transcribing", "Done", "Failed", "Cancelled"


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class App(BaseTk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("980x700")
        self.minsize(820, 560)

        s = load_settings()
        self.model_var = tk.StringVar(value=s.get("model", "medium"))
        self.lang_var = tk.StringVar(value=s.get("language", "Auto-detect"))
        self.out_var = tk.StringVar(value=s.get("output_dir", str(default_output_dir())))
        self.fmt_vars = {f: tk.BooleanVar(value=f in s.get("formats", ["txt"])) for f in ("txt", "srt", "vtt")}
        self.status_var = tk.StringVar(value="Add files to get started.")

        self.jobs: list[dict] = []          # {"path", "status", "iid", "outputs"}
        self.events: queue.Queue = queue.Queue()
        self.cancel_flag = threading.Event()
        self.worker: threading.Thread | None = None
        self.last_outputs: list[Path] = []

        self._build_style()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_events)
        self.after(300, self._startup_checks)

    # ------------------------------------------------------------------ layout

    def _build_style(self):
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 15))
        style.configure("Muted.TLabel", foreground="#666")
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 10))
        style.configure("Horizontal.TProgressbar", background="#2f6fbf")  # ignored by the native Windows theme

    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}

        header = ttk.Frame(self)
        header.pack(fill="x", **pad)
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=f"v{__version__}", style="Muted.TLabel").pack(side="left", padx=8, pady=(6, 0))

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.after(300, lambda: body.sashpos(0, 470))

        # ---- left: queue and settings
        left = ttk.Frame(body, width=440)
        body.add(left, weight=1)

        q = ttk.LabelFrame(left, text="Files")
        q.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(q, columns=("status", "length"), show="tree headings", height=8, selectmode="extended")
        self.tree.heading("#0", text="File")
        self.tree.heading("status", text="Status")
        self.tree.heading("length", text="Length")
        self.tree.column("#0", width=230)
        self.tree.column("status", width=95, anchor="center")
        self.tree.column("length", width=70, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=6, pady=(6, 0))
        self.tree.bind("<Double-1>", self._open_selected_output)

        hint = "Drag files here, or use Add files." if HAS_DND else "Use Add files to choose videos or audio."
        ttk.Label(q, text=hint, style="Muted.TLabel").pack(anchor="w", padx=6, pady=(2, 0))

        qb = ttk.Frame(q)
        qb.pack(fill="x", padx=6, pady=6)
        ttk.Button(qb, text="Add files...", command=self._add_files_dialog).pack(side="left")
        ttk.Button(qb, text="Remove", command=self._remove_selected).pack(side="left", padx=4)
        ttk.Button(qb, text="Clear finished", command=self._clear_finished).pack(side="left")

        if HAS_DND:
            for w in (self.tree, q):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)

        opts = ttk.LabelFrame(left, text="Settings")
        opts.pack(fill="x", pady=(8, 0))
        opts.columnconfigure(1, weight=1)

        ttk.Label(opts, text="Model").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        model_row = ttk.Frame(opts)
        model_row.grid(row=0, column=1, sticky="ew", padx=6)
        cb = ttk.Combobox(model_row, textvariable=self.model_var, values=MODELS, state="readonly", width=10)
        cb.pack(side="left")
        self.model_note = ttk.Label(model_row, style="Muted.TLabel")
        self.model_note.pack(side="left", padx=8)
        cb.bind("<<ComboboxSelected>>", lambda e: self._update_model_note())
        self._update_model_note()

        ttk.Label(opts, text="Language").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        ttk.Combobox(opts, textvariable=self.lang_var, values=[n for n, _ in LANGUAGES],
                     state="readonly", width=16).grid(row=1, column=1, sticky="w", padx=6)

        ttk.Label(opts, text="Save as").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        fr = ttk.Frame(opts)
        fr.grid(row=2, column=1, sticky="w", padx=6)
        for key, label in (("txt", "Text"), ("srt", "SRT subtitles"), ("vtt", "VTT subtitles")):
            ttk.Checkbutton(fr, text=label, variable=self.fmt_vars[key]).pack(side="left", padx=(0, 10))

        ttk.Label(opts, text="Save to").grid(row=3, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(opts, textvariable=self.out_var).grid(row=3, column=1, sticky="ew", padx=6)
        out = ttk.Frame(opts)
        out.grid(row=4, column=1, sticky="w", padx=6, pady=(2, 8))
        ttk.Button(out, text="Change folder...", command=self._choose_output).pack(side="left")
        ttk.Button(out, text="Open folder", command=self._open_output_dir).pack(side="left", padx=4)
        ttk.Button(out, text="Reset", command=lambda: self.out_var.set(str(default_output_dir()))).pack(side="left")

        # ---- right: live transcript
        right = ttk.LabelFrame(body, text="Live transcript")
        body.add(right, weight=2)
        self.text = tk.Text(right, wrap="word", font=("Segoe UI", 10), padx=10, pady=8,
                            relief="flat", borderwidth=0, state="disabled")
        sb = ttk.Scrollbar(right, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", pady=6)
        self.text.pack(fill="both", expand=True, padx=(6, 0), pady=6)
        self.text.tag_configure("ts", foreground="#2f6fbf", font=("Consolas", 9))
        self.text.tag_configure("info", foreground="#777", font=("Segoe UI", 9, "italic"))
        self.text.tag_configure("err", foreground="#b3261e")
        self.text.tag_configure("file", font=("Segoe UI Semibold", 11), spacing1=6, spacing3=4)

        # ---- bottom: progress and actions
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=12, pady=(0, 10))
        self.progress = ttk.Progressbar(bottom, mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        row = ttk.Frame(bottom)
        row.pack(fill="x", pady=(6, 0))
        # Buttons are packed first so a long status message can never push them off screen.
        self.cancel_btn = ttk.Button(row, text="Cancel", command=self._cancel, state="disabled")
        self.cancel_btn.pack(side="right")
        self.start_btn = ttk.Button(row, text="Transcribe", style="Accent.TButton", command=self._start)
        self.start_btn.pack(side="right", padx=6)
        self.open_btn = ttk.Button(row, text="Open last transcript", command=self._open_last, state="disabled")
        self.open_btn.pack(side="right")
        ttk.Label(row, textvariable=self.status_var, anchor="w").pack(side="left", fill="x", expand=True)

    # ------------------------------------------------------------------ queue management

    def _update_model_note(self):
        self.model_note.configure(text=MODEL_NOTES.get(self.model_var.get(), ""))

    def _add_paths(self, paths):
        added = 0
        for p in paths:
            p = os.path.normpath(p)
            if os.path.isdir(p):
                self._add_paths(str(c) for c in sorted(Path(p).iterdir()))
                continue
            if Path(p).suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            if any(j["path"] == p and j["status"] in (PENDING, WORKING) for j in self.jobs):
                continue
            dur = media_duration(p)
            iid = self.tree.insert("", "end", text=Path(p).name,
                                   values=(PENDING, format_timestamp(dur) if dur else "?"))
            self.jobs.append({"path": p, "status": PENDING, "iid": iid, "outputs": [], "duration": dur})
            added += 1
        if added:
            self.status_var.set(f"{self._count(PENDING)} file(s) ready.")

    def _add_files_dialog(self):
        exts = " ".join(f"*{e}" for e in sorted(MEDIA_EXTENSIONS))
        paths = filedialog.askopenfilenames(
            title="Choose videos or audio",
            filetypes=[("Video and audio", exts), ("All files", "*.*")],
        )
        self._add_paths(paths)

    def _on_drop(self, event):
        self._add_paths(self.tk.splitlist(event.data))

    def _remove_selected(self):
        for iid in self.tree.selection():
            job = self._job(iid)
            if job and job["status"] != WORKING:
                self.jobs.remove(job)
                self.tree.delete(iid)

    def _clear_finished(self):
        for job in [j for j in self.jobs if j["status"] in (DONE, FAILED, CANCELLED)]:
            self.jobs.remove(job)
            self.tree.delete(job["iid"])

    def _job(self, iid):
        return next((j for j in self.jobs if j["iid"] == iid), None)

    def _count(self, status):
        return sum(1 for j in self.jobs if j["status"] == status)

    def _set_status(self, job, status):
        job["status"] = status
        vals = list(self.tree.item(job["iid"], "values"))
        vals[0] = status
        self.tree.item(job["iid"], values=vals)

    # ------------------------------------------------------------------ actions

    def _choose_output(self):
        d = filedialog.askdirectory(title="Save transcripts to", initialdir=self.out_var.get())
        if d:
            self.out_var.set(os.path.normpath(d))

    def _open_output_dir(self):
        p = Path(self.out_var.get())
        p.mkdir(parents=True, exist_ok=True)
        open_path(p)

    def _open_last(self):
        if self.last_outputs:
            open_path(self.last_outputs[0])

    def _open_selected_output(self, _event=None):
        sel = self.tree.selection()
        job = self._job(sel[0]) if sel else None
        if job and job["outputs"]:
            open_path(job["outputs"][0])

    def _settings(self) -> dict:
        return {
            "model": self.model_var.get(),
            "language": self.lang_var.get(),
            "output_dir": self.out_var.get(),
            "formats": [f for f, v in self.fmt_vars.items() if v.get()],
        }

    def _start(self):
        pending = [j for j in self.jobs if j["status"] == PENDING]
        if not pending:
            messagebox.showinfo(APP_NAME, "Add at least one video or audio file first.")
            return
        settings = self._settings()
        if not settings["formats"]:
            messagebox.showwarning(APP_NAME, "Pick at least one format to save (.txt, .srt or .vtt).")
            return
        save_settings(settings)

        self.cancel_flag.clear()
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress.configure(value=0)
        lang_code = dict(LANGUAGES).get(settings["language"], "")
        self.worker = threading.Thread(
            target=self._run_queue, args=(pending, settings, lang_code), daemon=True
        )
        self.worker.start()

    def _cancel(self):
        self.cancel_flag.set()
        self.status_var.set("Cancelling after the current line...")
        self.cancel_btn.configure(state="disabled")

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_NAME, "A transcription is running. Stop it and quit?"):
                return
            self.cancel_flag.set()
        save_settings(self._settings())
        self.destroy()

    def _startup_checks(self):
        if not check_ffmpeg():
            self._append("ffmpeg was not found. Install it and make sure it's on your PATH "
                         "(see README), then restart the app.\n", "err")
        try:
            import whisper  # noqa: F401
        except ImportError:
            self._append("Whisper is not installed. Run:  pip install -r requirements.txt\n", "err")

    # ------------------------------------------------------------------ worker thread
    # Never touch widgets here; send events to the UI thread instead.

    def _run_queue(self, jobs, settings, lang_code):
        out_dir = Path(settings["output_dir"])
        total = len(jobs)
        for n, job in enumerate(jobs, 1):
            if self.cancel_flag.is_set():
                self.events.put(("status", job, CANCELLED))
                continue
            self.events.put(("begin", job, n, total))
            started = time.time()
            duration = job.get("duration") or 0

            def on_segment(seg: Segment, job=job, n=n):
                pct = min(seg.end / duration, 1.0) if duration else 0
                self.events.put(("segment", seg, ((n - 1) + pct) / total * 100))

            try:
                t = transcribe_file(
                    job["path"], settings["model"], lang_code or None,
                    on_segment=on_segment,
                    should_cancel=self.cancel_flag.is_set,
                    log=lambda msg: self.events.put(("info", msg)),
                )
                outputs = save_transcript(t, out_dir, settings["formats"])
                self.events.put(("done", job, outputs, time.time() - started, n, total))
            except TranscriptionCancelled:
                self.events.put(("status", job, CANCELLED))
                self.events.put(("info", "Cancelled."))
            except Exception as exc:  # show any failure in the UI instead of crashing
                self.events.put(("error", job, str(exc)))
        self.events.put(("finished",))

    # ------------------------------------------------------------------ UI-thread event handling

    def _append(self, text, tag=None):
        self.text.configure(state="normal")
        self.text.insert("end", text, tag)
        self.text.see("end")
        self.text.configure(state="disabled")

    def _poll_events(self):
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "begin":
                    _, job, n, total = ev
                    self._set_status(job, WORKING)
                    self.tree.see(job["iid"])
                    self._append(f"{Path(job['path']).name}\n", "file")
                    self.status_var.set(f"Transcribing {n} of {total}: {Path(job['path']).name}")
                elif kind == "segment":
                    _, seg, pct = ev
                    self._append(f"[{format_timestamp(seg.start)}]  ", "ts")
                    self._append(seg.text + "\n")
                    self.progress.configure(value=pct)
                elif kind == "info":
                    self._append(ev[1] + "\n", "info")
                elif kind == "status":
                    self._set_status(ev[1], ev[2])
                elif kind == "done":
                    _, job, outputs, secs, n, total = ev
                    job["outputs"] = outputs
                    self.last_outputs = outputs
                    self._set_status(job, DONE)
                    self.progress.configure(value=n / total * 100)
                    names = ", ".join(p.name for p in outputs)
                    self._append(f"Saved {names}  ({int(secs // 60)}m {int(secs % 60)}s)\n\n", "info")
                    self.open_btn.configure(state="normal")
                elif kind == "error":
                    _, job, msg = ev
                    self._set_status(job, FAILED)
                    self._append(f"Failed: {msg}\n\n", "err")
                elif kind == "finished":
                    done, failed = self._count(DONE), self._count(FAILED)
                    self.start_btn.configure(state="normal")
                    self.cancel_btn.configure(state="disabled")
                    msg = f"Finished. {done} done"
                    if failed:
                        msg += f", {failed} failed"
                    self.status_var.set(msg + f". Saved to the {Path(self.out_var.get()).name} folder.")
                    if done and not failed:
                        self.progress.configure(value=100)
        except queue.Empty:
            pass
        self.after(100, self._poll_events)


def main():
    # pythonw has no console, so stdout/stderr are None; Whisper's download progress bar needs somewhere to write.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    if sys.platform == "win32":
        try:  # crisp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    app = App()
    if len(sys.argv) > 1:  # files dropped onto the launcher or passed on the command line
        app._add_paths(sys.argv[1:])
    app.mainloop()


if __name__ == "__main__":
    main()
