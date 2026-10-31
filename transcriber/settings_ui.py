"""The Settings window.

A mixin for the App class in app.py."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from transcriber.fonts import PIP
from transcriber.core import (ENGINES, MODELS, default_output_dir, faster_available,
                              set_engine)
from transcriber.icons import info_icon
from transcriber.people import SHAPES
from transcriber.speakers import detection_status
from transcriber.ui_common import (APPEARANCES, LANGUAGES, MODEL_NOTES, save_settings,
                                   set_dark_titlebar)


class SettingsMixin:

    def _open_settings(self):
        if getattr(self, "_settings_win", None) and self._settings_win.winfo_exists():
            self._settings_win.lift()
            return
        win = tk.Toplevel(self)
        self._settings_win = win
        win.title("Settings")
        win.transient(self)
        win.resizable(False, False)
        outer = ttk.Frame(win, padding=(20, 14, 20, 18))
        outer.pack(fill="both", expand=True)
        left, right = ttk.Frame(outer), ttk.Frame(outer)        # two columns, so it fits on screen
        left.grid(row=0, column=0, sticky="n")
        ttk.Separator(outer, orient="vertical").grid(row=0, column=1, sticky="ns", padx=22)
        right.grid(row=0, column=2, sticky="n")
        for col in (left, right):
            col.columnconfigure(1, weight=1)
        frame = left
        row = [0]

        def section(title):
            if row[0]:
                ttk.Separator(frame).grid(row=row[0], column=0, columnspan=2, sticky="ew", pady=(10, 6))
                row[0] += 1
            ttk.Label(frame, text=title, style="Section.TLabel").grid(row=row[0], column=0, columnspan=2,
                                                                       sticky="w", pady=(0, 2))
            row[0] += 1

        def line(label, widget, pady=5, sticky="w"):
            if label:
                ttk.Label(frame, text=label).grid(row=row[0], column=0, sticky="nw", padx=(0, 14), pady=pady)
            widget.grid(row=row[0], column=1, sticky=sticky, pady=pady)
            row[0] += 1

        # ---- Transcription
        section("Transcription")
        mrow = ttk.Frame(frame)
        cb = ttk.Combobox(mrow, textvariable=self.model_var, values=MODELS, state="readonly", width=10)
        cb.pack(side="left")
        note = ttk.Label(mrow, text=MODEL_NOTES.get(self.model_var.get(), ""), style="Muted.TLabel")
        note.pack(side="left", padx=10)
        cb.bind("<<ComboboxSelected>>", lambda e: note.configure(text=MODEL_NOTES.get(self.model_var.get(), "")))
        line("Model", mrow)
        erow = ttk.Frame(frame)
        eng_var = tk.StringVar(value=ENGINES.get(self.engine_pref, ENGINES["auto"]))
        ecb = ttk.Combobox(erow, textvariable=eng_var, values=list(ENGINES.values()), state="readonly", width=16)
        ecb.pack(side="left")
        eng_note = ttk.Label(erow, style="Muted.TLabel")
        eng_note.pack(side="left", padx=(10, 4))
        info_icon(erow, self, "faster-whisper uses the same models and is about 4x faster. Automatic uses it when "
                              f"it's installed ({PIP} install faster-whisper), otherwise OpenAI Whisper."
                  ).pack(side="left")

        def engine_note():
            from transcriber.core import engine
            using = ENGINES[engine()]
            eng_note.configure(text=f"using {using}" if self.engine_pref == "auto" else
                               ("not installed" if self.engine_pref == "faster" and not faster_available() else ""))

        def engine_picked(_e=None):
            self.engine_pref = next(k for k, v in ENGINES.items() if v == eng_var.get())
            set_engine(self.engine_pref)
            engine_note()
            self._model_changed()          # load the model for the new engine in the background
        ecb.bind("<<ComboboxSelected>>", engine_picked)
        engine_note()
        line("Engine", erow)
        line("Language", ttk.Combobox(frame, textvariable=self.lang_var, values=[n for n, _ in LANGUAGES],
                                      state="readonly", width=16))
        vrow = ttk.Frame(frame)
        vocab_var = tk.StringVar(value=self.vocab)
        ttk.Entry(vrow, textvariable=vocab_var, width=38).pack(side="left")
        info_icon(vrow, self, "Names, places and terms Whisper should spell right, separated by commas "
                              "(e.g. Lamberto, Dolphin Mall, Experience Corps). Everyone in People is "
                              "included automatically, plus the words listed for each person (edit a person "
                              "to add theirs).").pack(side="left", padx=4)

        def vocab_changed(*_):
            self.vocab = vocab_var.get()
        vocab_var.trace_add("write", vocab_changed)
        line("Words to know", vrow)
        fr = ttk.Frame(frame)
        for key, label in (("txt", "Text"), ("srt", "SRT subtitles"), ("vtt", "VTT subtitles")):
            ttk.Checkbutton(fr, text=label, variable=self.fmt_vars[key]).pack(side="left", padx=(0, 12))
        line("Save as", fr)
        from transcriber import speech
        sp = ttk.Frame(frame)
        have = speech.available()
        ttk.Checkbutton(sp, text="Extract speech first" + ("" if have else "  (needs: pip install demucs)"),
                        variable=self.extract_speech, state="normal" if have else "disabled").pack(side="left")
        info_icon(sp, self, "Before Whisper hears a track, Demucs pulls the voice out of it and leaves game sound, "
                            "music and room noise behind: cleaner words from noisy recordings. It separates voice "
                            "from everything else, not one person from another, so another person caught by the "
                            "same mic is still there. Slow without a GPU; the first run downloads the model (about 80 MB).").pack(side="left", padx=4)
        line("", sp)
        line("Save to", ttk.Entry(frame, textvariable=self.out_var, width=42), pady=(5, 2), sticky="ew")
        out = ttk.Frame(frame)
        ttk.Button(out, text="Change folder...", command=lambda: self._choose_output(win)).pack(side="left")
        ttk.Button(out, text="Open folder", command=self._open_output_dir).pack(side="left", padx=4)
        ttk.Button(out, text="Reset", command=lambda: self.out_var.set(str(default_output_dir()))).pack(side="left")
        line("", out, pady=(2, 5))

        # ---- Speakers
        section("Speakers")
        det = ttk.Frame(frame)
        ttk.Checkbutton(det, text="Detect who's speaking", variable=self.detect_speakers).pack(side="left")
        info_icon(det, self, "After each transcription, labels every line with who's talking. Click a name to "
                             "say who it is. Needs pyannote.audio and a Hugging Face token.").pack(side="left", padx=4)
        line("", det)
        from transcriber.speakers import SPEAKER_MODELS, set_speaker_model
        smrow = ttk.Frame(frame)
        sm_var = tk.StringVar(value=SPEAKER_MODELS.get(self.speaker_model, SPEAKER_MODELS["pyannote"]))
        smcb = ttk.Combobox(smrow, textvariable=sm_var, values=list(SPEAKER_MODELS.values()), state="readonly",
                            width=44)
        smcb.pack(side="left")
        info_icon(smrow, self, "pyannote handles any number of people. NVIDIA Nemotron 3 is newer, notices people "
                               "talking over each other, and handles up to 8 people (fast on an NVIDIA card; "
                               "needs the NVIDIA models in What's installed). People are recognized the same way "
                               "with either.").pack(side="left", padx=4)

        def speaker_model_picked(_e=None):
            self.speaker_model = next(k for k, v in SPEAKER_MODELS.items() if v == sm_var.get())
            set_speaker_model(self.speaker_model)
            save_settings(self._settings())
            if self.speaker_model == "nemotron":
                from transcriber import setup_check
                if not setup_check._nvidia_component()["ok"]:
                    self.after(50, lambda: self._open_setup("nvidia"))
        smcb.bind("<<ComboboxSelected>>", speaker_model_picked)
        line("Speaker model", smrow)
        tok = ttk.Frame(frame)
        tok_entry = ttk.Entry(tok, textvariable=self.hf_token, width=36, show="\u2022")
        tok_entry.pack(side="left")
        shown = tk.BooleanVar(value=False)
        ttk.Checkbutton(tok, text="Show", variable=shown,
                        command=lambda: tok_entry.configure(show="" if shown.get() else "\u2022")).pack(side="left", padx=8)
        line("Hugging Face token", tok)
        ready, msg = detection_status()
        from transcriber.speakers import model_downloaded
        if ready and model_downloaded():
            short, msg = "Ready (downloaded)", "The speaker model is on this PC and works offline. The token isn't needed anymore."
        elif ready and self.hf_token.get().strip():
            short, msg = "Ready", "The speaker model downloads the first time it runs (the only time the token is used), then works offline."
        elif ready:
            short, msg = "Needs a token", "Create a Read token at huggingface.co/settings/tokens and paste it here."
        else:
            short = "Not installed"
        st = ttk.Frame(frame)
        ttk.Label(st, text=short, style="Muted.TLabel").pack(side="left")
        info_icon(st, self, msg).pack(side="left", padx=4)
        if not ready:
            ttk.Button(st, text="Install...", command=lambda: self._open_setup("speakers")).pack(side="left", padx=(8, 0))
        line("", st, pady=(0, 5))
        pb = ttk.Frame(frame)
        ttk.Button(pb, text="Manage people...", command=self._open_people).pack(side="left")
        ttk.Button(pb, text="What's installed...", command=lambda: self._open_setup()).pack(side="left", padx=6)
        info_icon(pb, self, "Everything the app uses, whether it's installed, and how to add what's missing.").pack(
            side="left")
        line("", pb, pady=(2, 5))

        def detect_toggled(*_):      # turning detection on without it installed: offer to install it
            if self.detect_speakers.get() and not detection_status()[0]:
                self.after(50, lambda: self._open_setup("speakers"))
        self._detect_trace = self.detect_speakers.trace_add("write", detect_toggled)
        win.bind("<Destroy>", lambda e: e.widget is win and self._drop_detect_trace(), add="+")

        # ---- Appearance
        frame = right                 # the right-hand column from here on
        row[0] = 0
        section("Appearance")
        theme_cb = ttk.Combobox(frame, textvariable=self.appearance_var, values=APPEARANCES, state="readonly", width=14)
        theme_cb.bind("<<ComboboxSelected>>", lambda e: (self._apply_theme(), set_dark_titlebar(win, self.current_theme == "dark")))
        line("Theme", theme_cb)
        shape_cb = ttk.Combobox(frame, textvariable=self.shape_label, values=list(SHAPES.values()),
                                state="readonly", width=14)
        shape_cb.bind("<<ComboboxSelected>>", lambda e: self._shape_changed())
        line("Profile pictures", shape_cb)

        # ---- Deleting
        # ---- Captions (the player's subtitles)
        section("Captions")
        from transcriber.player import DEFAULT_CAPTIONS, SIZE_PERCENTS, size_percent
        style = {**DEFAULT_CAPTIONS, **self.caption_style}
        style["size"] = size_percent(style)            # (older settings said small / medium / large)
        if style["size"] not in SIZE_PERCENTS:
            style["size"] = min(SIZE_PERCENTS, key=lambda p: abs(p - style["size"]))
        choices = {
            "mode": ("Style", {"below": "Below the video", "overlay": "On the video (movie style)"}),
            "size": ("Size", {p: f"{p}%" for p in SIZE_PERCENTS}),
            "background": ("Background", {"box": "See-through box", "solid": "Solid box", "outline": "Outline",
                                          "shadow": "Shadow"}),
            "color": ("Text color", {"white": "White", "yellow": "Yellow", "gray": "Light gray"}),
            "speaker": ("Speaker color", {"name": "Name only", "text": "Text in their color",
                                          "box": "Box in their color"}),
            "position": ("Position", {"bottom": "Bottom", "top": "Top"}),
            "overlap": ("Talking at once", {"layer": "Stacked (one caption each)", "dash": "One caption with dashes"}),
        }
        boxes = {}

        def caption_changed(_e=None):
            for key, (_label, opts) in choices.items():
                picked = boxes[key].get()
                style[key] = next((k for k, v in opts.items() if v == picked), style.get(key))
            style["names"] = names_var.get()
            for key, var in rule_vars.items():
                style[key] = var.get()
            on_video = style["mode"] == "overlay"
            for key in ("background", "position"):
                boxes[key].configure(state="readonly" if on_video else "disabled")
            split_changed = (style.get("split") != self.caption_style.get("split", True)
                             or style.get("overlap") != self.caption_style.get("overlap", "layer"))
            self.caption_style = dict(style)
            self.player.set_caption_style(self.caption_style)
            self._apply_caption_options()
            save_settings(self._settings())
            if split_changed and getattr(self, "display", None):
                self._redisplay()                  # the player's captions follow the setting

        for key, (label, opts) in choices.items():
            holder = ttk.Frame(frame) if key in ("mode", "speaker", "overlap") else frame
            cb = ttk.Combobox(holder, values=list(opts.values()), state="readonly", width=24 if key == "mode" else 16)
            cb.set(opts.get(style.get(key), next(iter(opts.values()))))
            cb.bind("<<ComboboxSelected>>", caption_changed)
            boxes[key] = cb
            if key == "speaker":
                cb.pack(side="left")
                ttk.Button(holder, text="Colors...", command=self._people_colors_dialog).pack(side="left", padx=(6, 0))
                info_icon(holder, self, "Each person's color (People > their color) on their captions: just the "
                                        "name, all the text, or a box behind it. Also used in burned-in clips."
                          ).pack(side="left", padx=6)
                line(label, holder)
                continue
            if key == "mode":
                cb.pack(side="left")
                info_icon(holder, self, "On the video puts the captions on the picture like a movie, in fullscreen "
                                        "too. Background and Position apply to that style. Audio files keep the "
                                        "strip below.").pack(side="left", padx=6)
                line(label, holder)
            elif key == "overlap":
                cb.pack(side="left")
                info_icon(holder, self, "When two or three people talk at once: Stacked gives each their own "
                                        "caption, one above the other, with its own name, color and timing "
                                        "(up to 3). Dashes puts both in one caption with a dash each, the old "
                                        "TV way, when both are short. Plain .srt files always use dashes. On the "
                                        "timeline, drag a line up to stack it by hand.").pack(side="left", padx=6)
                line(label, holder)
            else:
                line(label, cb)
        names_var = tk.BooleanVar(value=style.get("names", True))
        line("", ttk.Checkbutton(frame, text="Show speaker names", variable=names_var, command=caption_changed))
        rule_vars = {}
        for key, text, tip in (
                ("split", "Caption-sized pieces",
                 "Up to 2 lines of 42 characters and 7 seconds each, broken at natural places and timed by "
                 "the words, like TV and streaming captions. In the player, caption files and exports."),
                ("cuts", "Line up with scene cuts",
                 "In exports from a video, captions start and end on a nearby cut instead of just before or "
                 "after it. Only for stretches up to 30 minutes (it reads every frame)."),
                ("sounds", "Write sounds like [laughs]",
                 "New transcripts also note sounds such as [laughs] or [music]. Whisper catches some, not all.")):
            rule_vars[key] = tk.BooleanVar(value=bool(style.get(key)))
            holder = ttk.Frame(frame)
            ttk.Checkbutton(holder, text=text, variable=rule_vars[key], command=caption_changed).pack(side="left")
            info_icon(holder, self, tip).pack(side="left", padx=4)
            line("", holder)
        for key in ("background", "position"):
            boxes[key].configure(state="readonly" if style.get("mode") == "overlay" else "disabled")

        section("Timeline")
        keyrow = ttk.Frame(frame)
        key_btns = {}

        def show_keys():
            for tool, b in key_btns.items():
                b.configure(text=self.timeline_keys[tool].upper())

        def capture(tool):
            b = key_btns[tool]
            b.configure(text="Press a key")
            b.focus_set()

            def got(e):
                b.unbind("<KeyPress>")
                b.unbind("<FocusOut>")
                k = (e.char or "").lower()
                if e.keysym == "Escape" or not k:
                    show_keys()
                    return "break"
                if not k.isalnum() or k == "m":          # (M marks a line; space plays)
                    self.bell()
                    self.status_var.set("Pick a letter or number (M marks a line).")
                    show_keys()
                    return "break"
                for other in self.timeline_keys:
                    if other != tool and self.timeline_keys[other] == k:   # taken by another tool: swap them
                        self.timeline_keys[other] = self.timeline_keys[tool]
                self.timeline_keys[tool] = k
                save_settings(self._settings())
                show_keys()
                return "break"
            b.bind("<KeyPress>", got)
            b.bind("<FocusOut>", lambda e: (b.unbind("<KeyPress>"), b.unbind("<FocusOut>"), show_keys()))

        for tool, label in (("select", "Select"), ("snip", "Snip"), ("range", "Range")):
            ttk.Label(keyrow, text=label).pack(side="left", padx=(0 if tool == "select" else 14, 6))
            key_btns[tool] = ttk.Button(keyrow, width=11, command=lambda t=tool: capture(t))
            key_btns[tool].pack(side="left")
        show_keys()
        info_icon(keyrow, self, "Keys for the timeline's tools. Select moves and resizes lines; Snip splits a "
                                "line in two where you click; Range marks a stretch to play or export. Click a box, then press the new key.").pack(
            side="left", padx=6)
        line("Tool keys", keyrow)

        section("Deleting")
        nas = ttk.Frame(frame)
        ttk.Checkbutton(nas, text="Allow deleting on network drives (NAS)",
                        variable=self.allow_network_delete).pack(side="left")
        info_icon(nas, self, "Network files can't go to the Recycle Bin, so deleting there is permanent."
                  ).pack(side="left", padx=4)
        line("", nas)

        # ---- Updates
        from transcriber import __version__
        section("Updates")
        up = ttk.Frame(frame)
        ttk.Checkbutton(up, text="Check for updates automatically", variable=self.auto_update).pack(side="left")
        info_icon(up, self, "Once a day, the app asks GitHub if there's a newer version. If there is, an Update "
                            "button appears by the status text. Nothing installs without you clicking it.").pack(
            side="left", padx=4)
        line("", up)
        un = ttk.Frame(frame)
        ttk.Label(un, text=f"You have version {__version__}", style="Muted.TLabel").pack(side="left")
        ttk.Button(un, text="Check now", command=lambda: self._check_updates(quiet=False)).pack(side="left", padx=8)
        line("", un)
        from transcriber import crashlog
        lg = ttk.Frame(frame)
        ttk.Button(lg, text="Error logs...", command=crashlog.open_folder).pack(side="left")
        info_icon(lg, self, "If the app crashes or something goes wrong, the details are saved here "
                            "(nothing is sent anywhere), so the problem can be looked at.").pack(side="left", padx=4)
        line("", lg)

        def close(*_):
            save_settings(self._settings())
            self._refresh_saved(force=True)   # the save folder may have changed
            win.destroy()

        ttk.Button(outer, text="Done", style="Accent.TButton", command=close).grid(row=1, column=2, sticky="e",
                                                                               pady=(14, 0))
        win.protocol("WM_DELETE_WINDOW", close)
        win.bind("<Escape>", close)

        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + 60
        win.geometry(f"+{x}+{y}")
        set_dark_titlebar(win, self.current_theme == "dark")
        win.grab_set()
        win.focus_set()

    def _shape_changed(self):
        shape = next((k for k, v in SHAPES.items() if v == self.shape_label.get()), "circle")
        self.avatars.shape = shape
        save_settings(self._settings())
        self._redisplay()

    def _drop_detect_trace(self):
        t = getattr(self, "_detect_trace", None)
        if t:
            try:
                self.detect_speakers.trace_remove("write", t)
            except Exception:
                pass
            self._detect_trace = None
