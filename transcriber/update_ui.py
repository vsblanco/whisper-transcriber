"""Updates in the window: a quiet check once a day, a small "update available" button by the status
text, and the update window (what's new, then download and install, or git pull).

A mixin for the App class in app.py."""

from __future__ import annotations

import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk

from transcriber import __version__
from transcriber.fonts import ui_font
from transcriber.ui_common import set_dark_titlebar


class UpdateMixin:

    def _update_setup(self, row):
        """The "update available" button, next to the working indicator (hidden until needed)."""
        self._update_btn = ttk.Button(row, text="Update available", style="Accent.TButton",
                                      command=lambda: self._open_update(self._update_info))
        self._update_info = None

    def _auto_update_check(self):
        """Once a day, in the background. Quiet: no message unless there's something new."""
        from transcriber import updater
        if not self.auto_update.get() or not updater.due(self.last_update_check):
            return
        self._check_updates(quiet=True)

    def _check_updates(self, quiet: bool = False):
        from transcriber import updater

        def work():
            try:
                info = updater.check()
                err = None
            except Exception as exc:
                info, err = None, str(exc) or type(exc).__name__
            self.events.put(("call", lambda: self._update_checked(info, err, quiet)))
        if not quiet:
            self.status_var.set("Checking for updates...")
        threading.Thread(target=work, daemon=True).start()

    def _update_checked(self, info, err, quiet):
        self.last_update_check = time.time()
        self._save_settings_quietly()
        if err:
            if not quiet:
                self.status_var.set(f"Couldn't check for updates: {err}")
            return
        if not info:
            if not quiet:
                self.status_var.set(f"You have the newest version ({__version__}).")
            return
        self._update_info = info
        if quiet and info["version"] == self.skip_version:
            return
        self._update_btn.configure(text=f"Update to {info['version']}")
        if not self._update_btn.winfo_ismapped():
            self._update_btn.pack(side="right", padx=(0, 8), after=self.start_btn)
        self.status_var.set(f"Version {info['version']} is available.")
        if not quiet:
            self._open_update(info)

    def _open_update(self, info):
        if not info:
            return
        from transcriber import updater
        kind = updater.install_kind()
        win = tk.Toplevel(self)
        win.title("Update")
        win.transient(self)
        win.resizable(False, False)
        f = ttk.Frame(win, padding=(22, 18))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"Version {info['version']} is available", font=ui_font(13, bold=True)).pack(anchor="w")
        ttk.Label(f, text=f"You have {__version__}.", style="Muted.TLabel").pack(anchor="w", pady=(2, 10))
        p = self._palette()
        notes = tk.Text(f, height=12, width=72, wrap="word", relief="flat", borderwidth=0,
                        background=p["band"], foreground=p["fg"], font=ui_font(10), padx=10, pady=8)
        notes.insert("1.0", info["notes"][:4000] or "No notes for this version.")
        notes.configure(state="disabled")
        notes.pack(fill="x")
        bar = ttk.Progressbar(f, mode="determinate", maximum=1.0)
        step = ttk.Label(f, style="Muted.TLabel", wraplength=560, justify="left")
        step.pack(anchor="w", pady=(10, 0))
        buttons = ttk.Frame(f)
        buttons.pack(fill="x", pady=(14, 0))
        state = {"cancel": False, "busy": False}

        def skip():
            self.skip_version = info["version"]
            self._save_settings_quietly()
            self._update_btn.pack_forget()
            win.destroy()

        def page():
            webbrowser.open(info["page"])

        def go():
            if state["busy"]:
                return
            if kind in ("windows", "mac") and not self._edits_before_close():
                return                         # unsaved edits: they chose not to close
            state["busy"] = True
            for b in buttons.winfo_children():
                b.state(["disabled"])
            if kind in ("windows", "mac"):
                bar.pack(fill="x", pady=(8, 0), before=step)

            def post(fn):
                self.events.put(("call", fn))

            def work():
                try:
                    if kind == "git":
                        msg = updater.git_pull(lambda line: post(lambda line=line: step.configure(text=line)))
                        post(lambda: (step.configure(text=msg), close_btn.state(["!disabled"])))
                        return
                    path = updater.download(info["asset"], lambda fr, t: post(
                        lambda fr=fr, t=t: (bar.configure(value=fr), step.configure(text=t))), lambda: state["cancel"])
                    post(lambda: step.configure(text="Installing... the app will reopen by itself."))
                    updater.apply(path)
                    post(lambda: self.after(400, self._quit_for_update))
                except Exception as exc:
                    err = str(exc) or type(exc).__name__
                    post(lambda: (step.configure(text=f"The update didn't work: {err}"),
                                  [b.state(["!disabled"]) for b in buttons.winfo_children()],
                                  state.update(busy=False)))
            threading.Thread(target=work, daemon=True).start()

        close_btn = ttk.Button(buttons, text="Later", command=win.destroy)
        close_btn.pack(side="right")
        if kind in ("windows", "mac") and info.get("asset"):
            ttk.Button(buttons, text="Update now", style="Accent.TButton", command=go).pack(side="right", padx=(0, 8))
            step.configure(text="The new version downloads, the app closes, and it reopens updated.")
        elif kind == "git":
            ttk.Button(buttons, text="Update (git pull)", style="Accent.TButton", command=go).pack(side="right", padx=(0, 8))
            step.configure(text="This copy is a git clone: the update pulls the new code (or use GitHub Desktop: "
                                "Fetch origin, then Pull). Restart the app afterwards.")
        else:
            ttk.Button(buttons, text="Open download page", style="Accent.TButton", command=page).pack(
                side="right", padx=(0, 8))
            step.configure(text="Download the new version from GitHub.")
        ttk.Button(buttons, text="Skip this version", command=skip).pack(side="left")
        win.bind("<Escape>", lambda e: None if state["busy"] else win.destroy())
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_reqheight()) // 3
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        set_dark_titlebar(win, self.current_theme == "dark")

    def _quit_for_update(self):
        """Close for the installer (unsaved edits were already handled)."""
        try:
            self.cancel_flag.set()
            self._save_settings_quietly()
            self.player.release()
            from transcriber import crashlog
            crashlog.stop()                  # an update, not a crash
        finally:
            self.destroy()
