"""The loading screen shown at launch while the speech engine and model load."""

from __future__ import annotations

import importlib
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk

from transcriber import __version__
from transcriber.ui_common import APP_NAME, PALETTE
from transcriber.core import check_ffmpeg



class LoadingScreen(tk.Toplevel):
    """Shown at launch while PyTorch, Whisper and the selected model load, so the main window
    opens fully ready. A first-time model download shows real download progress."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.model = app.model_var.get()
        self.events: queue.Queue = queue.Queue()
        self.problems: list[str] = []
        self.finished = False

        p = PALETTE[app.current_theme or "light"]
        self.overrideredirect(True)
        self.configure(background=p["muted"], padx=1, pady=1)  # 1px outline around the borderless window
        self.attributes("-topmost", True)

        frame = ttk.Frame(self, padding=(28, 22))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text=f"Version {__version__}", style="Muted.TLabel").pack(anchor="w", pady=(0, 16))
        self.step_var = tk.StringVar(value="Starting up...")
        self.detail_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.step_var).pack(anchor="w")
        self.bar = ttk.Progressbar(frame, mode="indeterminate", length=380)
        self.bar.pack(fill="x", pady=(8, 4))
        self.bar.start(12)
        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Label(row, textvariable=self.detail_var, style="Muted.TLabel").pack(side="left")
        ttk.Button(row, text="Skip", command=self._done).pack(side="right")

        self.update_idletasks()
        w, h = max(self.winfo_reqwidth(), 440), self.winfo_reqheight()
        if app.saved_geometry:   # same monitor the main window will open on
            gw, gh, gx, gy = app.saved_geometry
            x, y = gx + (gw - w) // 2, gy + (gh - h) // 3
        else:
            x = (self.winfo_screenwidth() - w) // 2
            y = (self.winfo_screenheight() - h) // 3
        self.geometry(f"{w}x{h}+{x}+{y}")

        threading.Thread(target=self._load, daemon=True).start()
        self.after(80, self._poll)

    # ---- background thread: never touches widgets
    def _load(self):
        put = self.events.put
        put(("step", "Checking for ffmpeg...", ""))
        if not check_ffmpeg():
            self.problems.append(
                "ffmpeg was not found. On a Mac, install it with Homebrew:  brew install ffmpeg  then restart "
                "the app." if sys.platform == "darwin" else
                "ffmpeg was not found. Install it and make sure it's on your PATH (see README), then restart the app.")
        from transcriber.core import engine
        put(("step", "Loading the speech engine...", "This takes a few seconds the first time"))
        try:
            device = "CPU"
            try:
                import torch
                device = "GPU (CUDA)" if torch.cuda.is_available() else "CPU"
            except ImportError:
                if engine() != "faster":       # faster-whisper works without PyTorch
                    raise
            if engine() == "faster":
                put(("step", "Loading faster-whisper...", ""))
                import ctranslate2
                importlib.import_module("faster_whisper")
                device = "GPU (CUDA)" if ctranslate2.get_cuda_device_count() > 0 else "CPU"
            else:
                put(("step", "Loading Whisper...", f"Running on {device}"))
                importlib.import_module("whisper")
        except ImportError as exc:
            missing = getattr(exc, "name", None) or "a required package"
            self.problems.append(
                f"Couldn't load Whisper: {exc}\n"
                f"This app is running on the Python at:\n    {sys.executable}\n"
                f"Install the packages into that same Python with:\n"
                f'    "{sys.executable.replace("pythonw.exe", "python.exe")}" -m pip install -r requirements.txt'
                + ("" if missing in ("whisper", "torch") else f"\n(The missing piece is '{missing}'.)")
            )
            put(("done",))
            return

        try:
            from transcriber.core import load_model, model_is_downloaded
            if model_is_downloaded(self.model):
                put(("step", f"Loading the '{self.model}' model...", f"Running on {device}"))
            else:
                put(("step", f"Downloading the '{self.model}' model...", "First time only"))
            load_model(self.model, log=lambda m: None,
                       on_download=lambda done, total: put(("download", done, total)))
        except Exception as exc:
            self.problems.append(f"Couldn't load the '{self.model}' model: {exc}. "
                                 f"It will be retried when you transcribe.")
        put(("done",))

    # ---- UI thread
    def _poll(self):
        if self.finished:
            return
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[0] == "step":
                    self.step_var.set(ev[1])
                    self.detail_var.set(ev[2])
                elif ev[0] == "download":
                    done, total = ev[1], ev[2]
                    if total:
                        if str(self.bar.cget("mode")) != "determinate":
                            self.bar.stop()
                            self.bar.configure(mode="determinate", maximum=total)
                        self.bar.configure(value=done)
                        self.detail_var.set(f"{done / 1e6:,.0f} MB of {total / 1e6:,.0f} MB")
                elif ev[0] == "done":
                    self._done()
                    return
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _done(self):
        if self.finished:
            return
        self.finished = True
        self.bar.stop()
        self.destroy()
        self.app.show_ready(self.problems)
