"""The "What's installed" window: every piece the app uses, whether it's there, and how to get
what's missing (a command to copy, or an Install button that does it, with progress).

A mixin for the App class in app.py."""

from __future__ import annotations

import sys
import threading
import tkinter as tk
from tkinter import ttk

from transcriber.fonts import MAC, WINDOWS, ui_font
from transcriber.icons import info_icon
from transcriber.ui_common import set_dark_titlebar

OK, MISSING = "#16a34a", "#dc2626"
MONO = ("Consolas", 9) if WINDOWS else ("Menlo", 11) if MAC else ("DejaVu Sans Mono", 9)


class SetupMixin:

    def _open_setup(self, highlight: str = ""):
        if getattr(self, "_setup_win", None) and self._setup_win.winfo_exists():
            self._setup_win.lift()
            return
        win = tk.Toplevel(self)
        self._setup_win = win
        win.title("What's installed")
        win.transient(self)
        win.resizable(False, False)
        outer = ttk.Frame(win, padding=(22, 18))
        outer.pack(fill="both", expand=True)
        head = ttk.Frame(outer)
        head.pack(fill="x", pady=(0, 10))
        ttk.Label(head, text="What's installed", font=ui_font(13, bold=True)).pack(side="left")
        info_icon(head, self, "Everything the app uses. Missing pieces show how to add them: copy the command, or "
                              "click Install and the app does it for you.").pack(side="left", padx=6)
        rows = ttk.Frame(outer)
        rows.pack(fill="x")
        log_box = ttk.Frame(outer)
        bar = ttk.Progressbar(log_box, mode="determinate", maximum=1.0)
        bar.pack(fill="x", pady=(10, 4))
        step = ttk.Label(log_box, style="Muted.TLabel")
        step.pack(anchor="w")
        p = self._palette()
        log = tk.Text(log_box, height=7, width=86, wrap="none", font=MONO,
                      relief="flat", borderwidth=0, background=p["band"], foreground=p["muted"])
        log.pack(fill="x", pady=(4, 0))
        log.configure(state="disabled")
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(14, 0))
        close_btn = ttk.Button(buttons, text="Close", command=win.destroy)
        close_btn.pack(side="right")
        cancel_btn = ttk.Button(buttons, text="Cancel install")
        state = {"busy": False, "cancel": False, "install_btns": []}

        def add_log(line: str):
            if not win.winfo_exists():
                return
            log.configure(state="normal")
            log.insert("end", line + "\n")
            if int(log.index("end-1c").split(".")[0]) > 400:
                log.delete("1.0", "100.0")
            log.see("end")
            log.configure(state="disabled")

        def build():
            from transcriber.setup_check import components
            for w in rows.winfo_children():
                w.destroy()
            state["install_btns"], state["vars"] = [], []
            comps = components(vlc_ok=getattr(self.player, "available", None))
            for r, c in enumerate(comps):
                mark = tk.Label(rows, text="✓" if c["ok"] else "✗", fg=OK if c["ok"] else MISSING,
                                bg=ttk.Style(self).lookup("TFrame", "background") or p["bg"],
                                font=ui_font(12, bold=True))
                mark.grid(row=r * 2, column=0, sticky="nw", padx=(0, 10), pady=(8, 0))
                top = ttk.Frame(rows)
                top.grid(row=r * 2, column=1, sticky="w", pady=(8, 0))
                ttk.Label(top, text=c["name"], font=ui_font(10, bold=True)).pack(side="left")
                ttk.Label(top, text="  " + c["status"], style="Muted.TLabel").pack(side="left")
                info_icon(top, self, c["purpose"]).pack(side="left", padx=4)
                if c["ok"] and not (c["key"] == "gpu" and c["fix"]):
                    continue
                if not c["fix"] and not c["install"]:
                    continue
                low = ttk.Frame(rows)
                low.grid(row=r * 2 + 1, column=1, sticky="w", pady=(2, 0))
                command_like = any(x in c["fix"] for x in ("pip ", "brew ", "winget ", "apt "))
                if command_like:
                    cmd = c["fix"].split("   (")[0]
                    var = tk.StringVar(value=cmd)
                    state["vars"].append(var)          # (kept, or Tk forgets the text)
                    e = ttk.Entry(low, textvariable=var, width=max(min(len(cmd) + 2, 62), 20), state="readonly")
                    e.pack(side="left")
                    ttk.Button(low, text="Copy", command=lambda cmd=cmd: (self.clipboard_clear(), self.clipboard_append(cmd),
                                                                     self.status_var.set("Copied."))).pack(side="left", padx=4)
                    if "   (" in c["fix"]:
                        ttk.Label(low, text="(" + c["fix"].split("   (", 1)[1], style="Muted.TLabel").pack(side="left", padx=4)
                else:
                    ttk.Label(low, text=c["fix"], style="Muted.TLabel", wraplength=560).pack(side="left")
                if c["install"]:
                    b = ttk.Button(low, text="Install", style="Accent.TButton",
                                   command=lambda c=c: start(c))
                    b.pack(side="left", padx=(8, 0))
                    state["install_btns"].append(b)
                    if highlight and c["key"] == highlight:
                        b.focus_set()
            for b in state["install_btns"]:
                b.state(["disabled"] if state["busy"] else ["!disabled"])

        def finished(err, restart_note):
            state["busy"] = False
            cancel_btn.pack_forget()
            close_btn.state(["!disabled"])
            self._busy(False)
            if not win.winfo_exists():
                return
            if err == "cancelled":
                step.configure(text="Cancelled.")
            elif err and ("externally-managed" in err or "PEP 668" in err):
                step.configure(text="This Python is managed by your system (Homebrew), so packages can't be added "
                                    "to it. Run mac_setup.sh once: it gives the app its own Python.")
                add_log(err)
            elif err:
                step.configure(text="That didn't work: " + err.splitlines()[-1][:200])
                add_log(err)
            else:
                bar.configure(value=1.0)
                step.configure(text="Done." + (" Restart the app to use it." if restart_note else ""))
            build()

        def start(c):
            kind, packages, extra = c["install"]
            if kind == "addon" and not self._confirm_addon():
                return
            if kind == "nvidia":
                from transcriber import addons
                plan = addons.nvidia_install_plan()
                if plan["torch_gpu"] and sys.platform == "win32":
                    # PyTorch's files are in use while the app runs: install after it closes
                    if not self._confirm(title="Install the NVIDIA models",
                                         heading="Close the app to install?", body="", items=[],
                                         detail="PyTorch has to be replaced with its GPU version (about 3 GB), "
                                                "which can't happen while the app is using it. The app closes, a "
                                                "window shows the install, and the app opens again when it's done.",
                                         confirm="Close and install"):
                        return
                    if not self._edits_before_close():
                        return
                    if self.worker and self.worker.is_alive():
                        self.status_var.set("Wait for the transcription to finish, then install.")
                        return
                    addons.install_after_close(plan)
                    win.destroy()
                    self.after(300, self._quit_for_update)
                    return
                from transcriber import setup_check
                packages = ["transformers>=5.21"] + ([] if setup_check._has("torch") else ["torch"])
                extra = ["--upgrade"]
            state.update(busy=True, cancel=False)
            if not log_box.winfo_ismapped():
                log_box.pack(fill="x", before=buttons)
            bar.configure(value=0)
            step.configure(text=f"Installing {c['name'].lower()}...")
            cancel_btn.configure(command=lambda: state.update(cancel=True))
            cancel_btn.pack(side="right", padx=(0, 8))
            close_btn.state(["disabled"])
            self._busy(True, f"Installing {c['name'].lower()}")
            for b in state["install_btns"]:
                b.state(["disabled"])

            def post(fn):
                self.events.put(("call", fn))

            def work():
                from transcriber import addons
                err, restart = None, False
                try:
                    if kind == "addon_nvidia":
                        post(lambda: bar.configure(mode="indeterminate") or bar.start(12))
                        addons.install_nvidia(lambda line: post(lambda line=line: add_log(line)),
                                              lambda: state["cancel"])
                    elif kind == "addon":
                        addons.install_speakers(
                            lambda line: post(lambda line=line: add_log(line)),
                            lambda f, t: post(lambda f=f, t=t: (bar.configure(value=f), step.configure(text=t))
                                              if win.winfo_exists() else None),
                            lambda: state["cancel"], gpu=self._addon_gpu)
                    else:
                        post(lambda: bar.configure(mode="indeterminate") or bar.start(12))
                        addons.pip_install(packages, lambda line: post(lambda line=line: add_log(line)),
                                           lambda: state["cancel"], extra)
                        restart = c["key"] in ("vlc", "dnd", "theme", "gpu", "engine", "pillow", "nvidia")
                except addons.Cancelled:
                    err = "cancelled"
                except Exception as exc:
                    err = str(exc) or type(exc).__name__
                post(lambda: (bar.stop(), bar.configure(mode="determinate")) if win.winfo_exists() else None)
                post(lambda: finished(err, restart))

            threading.Thread(target=work, daemon=True).start()

        def on_close():
            if state["busy"]:
                return
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", on_close)
        win.bind("<Escape>", lambda e: on_close())
        build()
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_reqheight()) // 4
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        set_dark_titlebar(win, self.current_theme == "dark")

    def _confirm_addon(self) -> bool:
        """Before downloading the speaker detection add-on: say how big it is (and offer the GPU
        version when there's an NVIDIA card)."""
        from transcriber import addons
        card = addons.nvidia_gpu()
        self._addon_gpu = card
        if not card:
            return self._confirm(title="Install speaker detection", heading="Download speaker detection?",
                                 body="", items=[], detail=f"It's a one-time download ({addons.download_size_note(False)}). "
                                 "You can keep using the app while it installs.", confirm="Download")
        choice = {"gpu": True}
        win = tk.Toplevel(self)
        win.title("Install speaker detection")
        win.transient(self)
        win.resizable(False, False)
        f = ttk.Frame(win, padding=(22, 18))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Download speaker detection?", font=ui_font(12, bold=True)).pack(anchor="w")
        var = tk.StringVar(value="gpu")
        ttk.Radiobutton(f, text=f"GPU version, much faster ({addons.download_size_note(True)})", variable=var,
                        value="gpu").pack(anchor="w", pady=(10, 2))
        ttk.Radiobutton(f, text=f"CPU version, smaller ({addons.download_size_note(False)})", variable=var,
                        value="cpu").pack(anchor="w")
        ttk.Label(f, text="It's a one-time download. You can keep using the app while it installs.",
                  style="Muted.TLabel").pack(anchor="w", pady=(10, 0))
        result = {"ok": False}
        bt = ttk.Frame(f)
        bt.pack(fill="x", pady=(16, 0))

        def done(ok):
            result["ok"] = ok
            choice["gpu"] = var.get() == "gpu"
            win.destroy()
        ttk.Button(bt, text="Download", style="Accent.TButton", command=lambda: done(True)).pack(side="right")
        ttk.Button(bt, text="Cancel", command=lambda: done(False)).pack(side="right", padx=(0, 8))
        win.bind("<Escape>", lambda e: done(False))
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_reqheight()) // 3
        win.geometry(f"+{x}+{y}")
        set_dark_titlebar(win, self.current_theme == "dark")
        win.grab_set()
        self.wait_window(win)
        self._addon_gpu = choice["gpu"]
        return result["ok"]
