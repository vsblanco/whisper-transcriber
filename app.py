"""Whisper Transcriber desktop app.

Drop in video or audio files, pick a model, and get timestamped transcripts
saved to Documents\\Transcripts (or any folder you choose).

Run:  pythonw app.py      (or double-click "Transcriber.pyw")
"""

from __future__ import annotations

import os
import bisect
import queue
import re
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from typing import Optional
from tkinter import filedialog, messagebox, ttk

from transcriber.fonts import RIGHT_CLICK, ui_font
from transcriber import __version__
from transcriber.editing import EditingMixin
from transcriber.finder import FindMixin
from transcriber.icons import Tooltip, icon_button, setup_styles as setup_icon_styles
from transcriber.people_ui import PeopleMixin
from transcriber.search_ui import SearchMixin
from transcriber.session_ui import SessionMixin
from transcriber.markers_ui import MarkersMixin
from transcriber.review_ui import ReviewMixin
from transcriber.checks_ui import ChecksMixin
from transcriber.setup_ui import SetupMixin
from transcriber.update_ui import UpdateMixin
from transcriber.settings_ui import SettingsMixin
from transcriber.tools_ui import ToolsMixin
from transcriber.tracks_ui import TracksMixin
from transcriber.timeline_ui import TimelineMixin
from transcriber.record_ui import RecordMixin
from transcriber.ui_common import (
    APP_NAME,
    DONE,
    DROP_ACCENT,
    FAILED,
    LANGUAGES,
    MODEL_NOTES,
    PALETTE,
    PENDING,
    WORKING,
    _GEOMETRY,
    load_settings,
    make_icon,
    open_path,
    point_on_a_monitor,
    save_settings,
    set_dark_titlebar,
    short_length,
    system_theme,
)
from transcriber.people import SHAPES, AvatarCache, People
from transcriber.player import Player
from transcriber.speakers import rows_for
from transcriber.core import (
    AUDIO_EXTENSIONS,
    MODELS,
    default_output_dir,
    format_timestamp,
    has_video_stream,
    is_model_loaded,
    set_engine,
)

from transcriber.optional import HAS_DND, HAS_SV, BaseTk, sv_ttk
from transcriber.queue_ui import QueueMixin
from transcriber.saved_ui import SavedMixin
from transcriber.loading_ui import LoadingScreen


class App(QueueMixin, SavedMixin, FindMixin, EditingMixin, PeopleMixin, TracksMixin, SettingsMixin, SearchMixin, ToolsMixin, SessionMixin,
          MarkersMixin, ReviewMixin, ChecksMixin, SetupMixin, UpdateMixin, TimelineMixin, RecordMixin, BaseTk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        s = load_settings()
        # With DPI awareness Tk sizes are real pixels, so scale the window for 125%/150% displays.
        self.ui_scale = max(float(self.tk.call("tk", "scaling")) / (96 / 72), 1.0)
        sc = self.ui_scale
        w = min(int(1100 * sc), self.winfo_screenwidth() - 40)
        h = min(int(820 * sc), self.winfo_screenheight() - 80)
        self.geometry(f"{w}x{h}")
        self.minsize(min(int(900 * sc), w), min(int(680 * sc), h))
        # Reopen where it was last closed (including on a second monitor), if that spot still exists.
        self.saved_geometry = None
        self._normal_geometry = None
        self._start_zoomed = bool(s.get("zoomed"))
        m = _GEOMETRY.match(str(s.get("window", "")))
        if m:
            gw, gh, gx, gy = map(int, m.groups())
            if point_on_a_monitor(gx + gw // 2, gy + 20):
                self.geometry(f"{gw}x{gh}+{gx}+{gy}")
                self.saved_geometry = (gw, gh, gx, gy)
        self.bind("<Configure>", self._remember_geometry, add="+")

        self.model_var = tk.StringVar(value=s.get("model", "medium"))
        self.lang_var = tk.StringVar(value=s.get("language", "Auto-detect"))
        self.out_var = tk.StringVar(value=s.get("output_dir", str(default_output_dir())))
        self.fmt_vars = {f: tk.BooleanVar(value=f in s.get("formats", ["txt"])) for f in ("txt", "srt", "vtt")}
        self.appearance_var = tk.StringVar(value=s.get("appearance", "System"))
        # Safety: deleting on a network drive (NAS) is permanent, so it's off until turned on in Settings.
        self.allow_network_delete = tk.BooleanVar(value=bool(s.get("allow_network_delete", False)))
        # Nicknames for audio tracks, keyed by the track's original name ("Track 2" -> "Discord"),
        # so one nickname covers every recording made with the same setup.
        self.track_nicknames: dict[str, str] = dict(s.get("track_nicknames", {}))
        # Speakers: who's talking, recognized from voice samples you tag (People).
        self.detect_speakers = tk.BooleanVar(value=bool(s.get("detect_speakers", False)))
        self.extract_speech = tk.BooleanVar(value=bool(s.get("extract_speech", False)))
        from transcriber import core as _core
        _core.EXTRACT_SPEECH = bool(self.extract_speech.get())
        self.extract_speech.trace_add("write", lambda *a: setattr(_core, "EXTRACT_SPEECH", bool(self.extract_speech.get())))
        self.hf_token = tk.StringVar(value=s.get("hf_token", ""))
        self.left_width = int(s.get("left_width") or 0)     # 0 = automatic
        self.vocab = s.get("vocab", "")                     # names and terms Whisper should know
        self.auto_update = tk.BooleanVar(value=bool(s.get("auto_update", True)))
        self.last_update_check = float(s.get("last_update_check") or 0)
        self.skip_version = s.get("skip_version", "")
        self.track_default = s.get("track_default", "each")
        self.track1_mix = tk.BooleanVar(value=bool(s.get("track1_is_mix", False)))
        self.engine_pref = s.get("engine", "auto")
        self.record_prefs = s.get("record") if isinstance(s.get("record"), dict) else {}
        self.speaker_model = s.get("speaker_model", "pyannote")
        self.ts_millis = bool(s.get("ts_millis", False))
        self.caption_style = s.get("captions") or {}
        self.transcript_view = "paragraphs" if s.get("transcript_view") == "paragraphs" else "lines"
        self._expanded: set = set()          # paragraphs opened into their lines
        self.timeline_open = bool(s.get("timeline_open", False))
        self.timeline_h = int(s.get("timeline_h") or 0) or int(150 * self.ui_scale)
        keys = s.get("timeline_keys") if isinstance(s.get("timeline_keys"), dict) else {}
        self.timeline_keys = {"select": str(keys.get("select") or "s")[:1].lower(),
                              "snip": str(keys.get("snip") or "b")[:1].lower(),
                              "range": str(keys.get("range") or "r")[:1].lower()}
        self._apply_caption_options()
        set_engine(self.engine_pref)
        from transcriber.speakers import set_speaker_model
        set_speaker_model(self.speaker_model)
        self.people = People()
        self.avatars = AvatarCache()
        self.avatars.shape = s.get("avatar_shape", "circle") if s.get("avatar_shape") in SHAPES else "circle"
        self.shape_label = tk.StringVar(value=SHAPES[self.avatars.shape])
        self.status_var = tk.StringVar(value="Add files to get started.")
        self.summary_var = tk.StringVar()
        self.search_var = tk.StringVar()
        self._search_dirty = False
        self.saved_count_var = tk.StringVar()
        self.current_theme = None

        # Queue state
        self.jobs: list[dict] = []          # {"path","status","iid","outputs","duration","icon","log"}
        self.events: queue.Queue = queue.Queue()
        self.cancel_flag = threading.Event()
        self.worker: threading.Thread | None = None

        # What the right-hand panel is showing: ("job", iid), ("saved", iid or None) or None (general)
        self.display = None
        self.follow_live = True             # auto-show the file being transcribed until the user picks another
        self.general_log: list[tuple[str, str | None]] = []

        # Saved transcripts state
        self.saved_items: dict[str, dict] = {}
        self.saved_sig = None

        # Thumbnails: sized for the screen's DPI, made on a background thread, cached on disk.
        scale = self.ui_scale
        self.thumb_w, self.thumb_h = round(64 * scale), round(36 * scale)
        self.thumb_dir = Path(tempfile.gettempdir()) / "WhisperTranscriber" / "thumbs"
        self.thumb_q: queue.Queue = queue.Queue()
        self.thumb_images: dict[str, tk.PhotoImage] = {}   # keep references or Tk drops the images
        self.icons: dict[str, tk.PhotoImage] = {}
        threading.Thread(target=self._thumb_worker, daemon=True).start()

        self._build_ui()
        self._apply_theme()
        # Load the Transcripts tab in the background now, so switching to it is instant.
        self.after(300, lambda: self._refresh_saved(force=True))
        for var in (self.model_var, self.lang_var, self.out_var, *self.fmt_vars.values()):
            var.trace_add("write", lambda *_: self._update_summary())
        self._update_summary()
        self._show_general()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_events)
        self.after(2000, self._watch_system_theme)
        self.after(3000, self._watch_saved_folder)

    # ------------------------------------------------------------------ theme

    def _wanted_theme(self) -> str:
        choice = self.appearance_var.get()
        return system_theme() if choice == "System" else choice.lower()

    def _palette(self) -> dict:
        return PALETTE[self.current_theme or "light"]

    def _apply_theme(self, force: bool = False):
        mode = self._wanted_theme()
        if mode == self.current_theme and not force:
            return
        self.current_theme = mode
        p = PALETTE[mode]
        if getattr(self, "_tl", None) is not None:          # the timeline draws its own colors
            self.after_idle(lambda: (ttk.Style(self).configure("Tiny.TButton", padding=(4, 1)), self._tl.redraw(),
                                     self._tl_sash.configure(background=self._palette()["hover"])))
        style = ttk.Style(self)

        if HAS_SV:
            sv_ttk.set_theme(mode, self)
        else:  # no sv-ttk: native look in light mode, a hand-colored "clam" in dark mode
            if mode == "light" and "vista" in style.theme_names():
                style.theme_use("vista")
            else:
                style.theme_use("clam")
                if mode == "dark":
                    bg = "#2b2b2b"
                    style.configure(".", background=bg, foreground=p["fg"], fieldbackground="#333333")
                    style.configure("Treeview", background="#333333", fieldbackground="#333333", foreground=p["fg"])
                    style.configure("TLabelframe.Label", background=bg, foreground=p["fg"])
                    style.map("TCombobox", fieldbackground=[("readonly", "#333333")], foreground=[("readonly", p["fg"])])
                    self.configure(background=bg)
                style.configure("Horizontal.TProgressbar", background="#2f6fbf")

        # Style settings belong to the active theme, so they're re-applied after every switch.
        style.configure("Title.TLabel", font=ui_font(15, bold=True))
        style.configure("Muted.TLabel", foreground=p["muted"])
        style.configure("Section.TLabel", font=ui_font(11, bold=True))
        # Tk has no text opacity, so "faint" is a lighter shade that blends toward the background.
        style.configure("Faint.TLabel", foreground="#b4b4b4" if mode == "light" else "#5c5c5c", font=ui_font(9))
        self._paint_trash()      # (its color follows the theme)
        setup_icon_styles(self)
        if hasattr(self, "_sash"):
            self.after_idle(self._sash_theme)
        chip_bg, chip_hover = ("#ececec", "#dcdcdc") if mode == "light" else ("#2d2d2d", "#3a3a3a")
        style.configure("Chip.TLabel", background=chip_bg, foreground=p["fg"])
        style.configure("ChipHover.TLabel", background=chip_hover, foreground=DROP_ACCENT[mode])
        style.configure("Treeview", rowheight=self.thumb_h + 10, indent=0)

        def strip_indicator(layout):  # flat lists: drop the expand-arrow slot so thumbnails sit at the edge
            out = []
            for name, opts in layout:
                if "indicator" in name.lower():
                    continue
                opts = dict(opts)
                if "children" in opts:
                    opts["children"] = strip_indicator(opts["children"])
                out.append((name, opts))
            return out
        try:
            style.layout("Treeview.Item", strip_indicator(style.layout("Treeview.Item")))
        except tk.TclError:
            pass

        self.text.configure(background=p["bg"], foreground=p["fg"], insertbackground=p["fg"],
                            selectbackground=p["select"], selectforeground=p["fg"])
        self.text.tag_configure("ts", foreground=p["ts"])
        self.text.tag_configure("info", foreground=p["info"])
        self.text.tag_configure("err", foreground=p["err"])
        self.text.tag_configure("band", background=p["band"])   # every other line, like spreadsheet rows
        self.text.tag_lower("band")
        self.text.tag_configure("hover", background=p["hover"])   # subtle: the row under the mouse
        self.text.tag_raise("hover", "band")
        self.text.tag_configure("now", background=p["now"])       # blue: the row the player is on
        self.text.tag_configure("mutedrow", foreground="#b4b4b4" if mode == "light" else "#5e5e5e")  # muted track
        speaker_colors = (["#2f6fbf", "#b0561b", "#2e8b57", "#8e44ad"] if mode == "light"
                          else ["#79b0ff", "#ffb07a", "#7ddca0", "#d1a3ff"])
        for i, color in enumerate(speaker_colors):
            self.text.tag_configure(f"spk{i}", foreground=color, font=ui_font(10, bold=True))
            self.text.tag_configure(f"trk{i}", foreground=color, font=ui_font(8))
        self.text.tag_raise("now", "hover")
        self.text.tag_raise("sel")
        if hasattr(self, "edit_bar"):
            self._edit_theme()
        if hasattr(self, "_find_box"):
            self._find_theme()

        self.icons = {k: make_icon(k, self.thumb_w, self.thumb_h, mode) for k in ("audio", "video", "pending", "doc")}
        for job in self.jobs:  # re-color the drawn icons; real video frames stay as they are
            if job.get("icon"):
                self.tree.item(job["iid"], image=self.icons[job["icon"]])
        for iid, item in self.saved_items.items():
            if item.get("icon"):
                self.saved.item(iid, image=self.icons[item["icon"]])
        set_dark_titlebar(self, mode == "dark")

    def _watch_system_theme(self):
        """Follow Windows light/dark changes while the app is open."""
        if self.appearance_var.get() == "System":
            self._apply_theme()
        self.after(2000, self._watch_system_theme)

    # ------------------------------------------------------------------ layout

    def _build_ui(self):
        header = ttk.Frame(self)
        header.pack(fill="x", padx=12, pady=(10, 6))
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=f"v{__version__}", style="Faint.TLabel").pack(side="left", padx=8, pady=(7, 0))
        ttk.Button(header, text="⚙  Settings", command=self._open_settings).pack(side="right")
        ttk.Button(header, text="People", command=self._open_people).pack(side="right", padx=(0, 6))
        ttk.Label(header, textvariable=self.summary_var, style="Muted.TLabel").pack(side="right", padx=(4, 10))
        # Model "chip": hover to highlight, click for a quick model menu right here.
        self.model_chip = ttk.Label(header, textvariable=self.model_var, style="Chip.TLabel", cursor="hand2", padding=(8, 3))
        self.model_chip.pack(side="right")
        self.model_chip.bind("<Enter>", lambda e: self.model_chip.configure(style="ChipHover.TLabel"))
        self.model_chip.bind("<Leave>", lambda e: self.model_chip.configure(style="Chip.TLabel"))
        self.model_chip.bind("<Button-1>", lambda e: (self._show_model_menu(), "break")[1])
        self.model_popup = None

        # ---- bottom: progress and actions
        bottom = ttk.Frame(self)
        bottom.pack(side="bottom", fill="x", padx=12, pady=(0, 10))
        self.progress = ttk.Progressbar(bottom, mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        row = ttk.Frame(bottom)
        row.pack(fill="x", pady=(6, 0))
        # Buttons are packed first so a long status message can never push them off screen.
        self.cancel_btn = ttk.Button(row, text="Cancel", command=self._cancel, state="disabled")
        self.cancel_btn.pack(side="right")
        self.start_btn = ttk.Button(row, text="Transcribe", style="Accent.TButton", command=self._start)
        self.start_btn.pack(side="right", padx=6)
        # Shown while something runs in the background (a clip export, redoing lines, speakers...).
        self._busy_box = ttk.Frame(row)
        self._busy_bar = ttk.Progressbar(self._busy_box, mode="indeterminate", length=int(70 * self.ui_scale))
        self._busy_bar.pack(side="left")
        self._busy_lbl = ttk.Label(self._busy_box, style="Muted.TLabel")
        self._busy_lbl.pack(side="left", padx=(6, 0))
        self._busy_jobs: list = []
        self._update_setup(row)
        ttk.Label(row, textvariable=self.status_var, anchor="w").pack(side="left", fill="x", expand=True)


        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        body.columnconfigure(0, minsize=int(450 * self.ui_scale), weight=0)   # left column keeps a steady width
        body.columnconfigure(1, minsize=int(10 * self.ui_scale), weight=0)   # the divider: drag to resize
        body.columnconfigure(2, weight=1)                # transcript takes the rest
        body.rowconfigure(0, weight=1)
        self._body = body
        body.bind("<Configure>", self._on_body_resize)

        # ---- left: Queue and Transcripts tabs
        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew")
        self._sash = tk.Frame(body, width=int(10 * self.ui_scale), cursor="sb_h_double_arrow", highlightthickness=0)
        self._sash.grid(row=0, column=1, sticky="ns")
        self._sash_line = tk.Frame(self._sash, width=2, highlightthickness=0)
        self._sash.bind("<Enter>", lambda e: self._sash_line.place(relx=0.5, rely=0.5, anchor="center",
                                                                   relheight=0.12, width=3))
        self._sash.bind("<Leave>", lambda e: None if self._sash_drag else self._sash_line.place_forget())
        self._sash.bind("<ButtonPress-1>", self._sash_press)
        self._sash.bind("<B1-Motion>", self._sash_move)
        self._sash.bind("<ButtonRelease-1>", self._sash_release)
        self._sash.bind("<Double-1>", self._sash_reset)
        for w in (self._sash, self._sash_line):
            w.configure(cursor="sb_h_double_arrow")
        self._sash_line.bind("<ButtonPress-1>", self._sash_press)
        self._sash_line.bind("<B1-Motion>", self._sash_move)
        self._sash_line.bind("<ButtonRelease-1>", self._sash_release)
        self._sash_line.bind("<Double-1>", self._sash_reset)
        self._sash_drag = None
        self.after_idle(self._sash_theme)
        # ---- left: the preview player, the whole height of the window
        box = ttk.LabelFrame(left, text="Preview")
        # the subtitle timeline (Timeline button in the header) goes under the player, as wide as
        # this column; the picture makes room for it
        tl_holder = ttk.Frame(left)
        tl_holder.pack(side="bottom", fill="x")
        box.pack(fill="both", expand=True)
        self._preview_box = box
        self._left_col = left
        self._timeline_setup(tl_holder, header)
        self.player = Player(box)
        self.player.fill_height = True       # the picture area fills the column (letterboxed)
        self.player.pack(fill="x", padx=8, pady=8)
        self.player.on_time = self._on_player_time
        self.player.on_tracks_changed = self._on_tracks_muted
        self.player.on_caption_size = self._set_caption_size
        self.player.on_caption_edit = self._edit_at_caption
        self.player.on_save_with_captions = self._save_with_captions
        self.player.set_caption_style(self.caption_style)

        # ---- right: one row of tabs. Transcript (what's shown), Queue (files to transcribe) and
        # Library (everything saved). Clicking a file in Queue or Library opens it in Transcript; the
        # transcript follows whichever of the two lists you were last on (self._list_mode).
        rightcol = ttk.Frame(body)
        rightcol.grid(row=0, column=2, sticky="nsew")
        self._rightcol = rightcol
        self.tabs = ttk.Notebook(rightcol)
        self.tabs.pack(fill="both", expand=True)
        self.right = ttk.Frame(self.tabs)
        self.tabs.add(self.right, text="Transcript")
        self.title_label = ttk.Label(self.right, text="", font=ui_font(11, bold=True), anchor="w")
        self.title_label.pack(fill="x", padx=10, pady=(8, 0))
        self._list_mode = "queue"
        self._build_queue_tab()
        self._build_saved_tab()
        self.tabs.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        for n, name in enumerate(("transcript", "queue", "library"), 1):
            self.bind(f"<Control-Key-{n}>", lambda e, name=name: self._show_tab(name) or "break")
        self.text = tk.Text(self.right, wrap="word", font=ui_font(10), padx=10, pady=8,
                            relief="flat", borderwidth=0, highlightthickness=0, state="disabled", spacing1=3, spacing3=3)
        self._band_n = 0
        self._who_by_line: dict[int, tuple] = {}  # text line -> (version files, speaker label)
        self._rows: list[tuple[int, int]] = []   # (start seconds, text line) for each timestamped row
        self._now_line = None
        # Version picker, shown above the text only for saved transcripts.
        self.version_bar = ttk.Frame(self.right)
        self.track_title = ttk.Label(self.version_bar, text="Show")
        self.track_var = tk.StringVar()
        self.track_cb = ttk.Combobox(self.version_bar, textvariable=self.track_var, state="readonly", width=14)
        self.track_cb.bind("<<ComboboxSelected>>", self._on_track_picked)
        self.saved_track: dict[str, str] = {}       # transcript name -> chosen track
        self._track_cache: dict[str, list] = {}     # source file -> its audio tracks
        self._silence_pending: set = set()
        self.version_title = ttk.Label(self.version_bar, text="Version")
        self.version_title.pack(side="left", padx=(2, 8))
        self.version_var = tk.StringVar()
        self.version_cb = ttk.Combobox(self.version_bar, textvariable=self.version_var, state="readonly", width=32)
        self.version_cb.pack(side="left")
        self.version_cb.bind("<<ComboboxSelected>>", self._on_version_picked)
        self._del_tip = "Delete this version"
        self.version_del_btn = icon_button(self.version_bar, self, "delete", lambda: self._del_tip, self._delete_version)
        self._paint_trash()
        self.version_del_btn.pack(side="right")
        # Lines / Paragraphs: a toggle that stays pressed while the transcript is shown as paragraphs.
        self.view_var = tk.BooleanVar(value=self.transcript_view == "paragraphs")
        self.view_btn = ttk.Checkbutton(self.version_bar, text="\u00b6", width=2, variable=self.view_var,
                                        style="Toggle.TButton" if HAS_SV else "TCheckbutton",
                                        command=lambda: self._set_view("paragraphs" if self.view_var.get() else "lines"))
        self.view_btn.pack(side="right", padx=(0, 6))
        Tooltip(self.view_btn, "Paragraphs: group lines by speaker. Click \u25b8 or double-click a paragraph to see its lines.")
        self.speakers_btn = icon_button(self.version_bar, self, "person", "Detect who's speaking in this transcript",
                                        self._add_speakers_later)
        self.stats_btn = icon_button(self.version_bar, self, "stats", "Talk time", self._show_talk_time)
        self.history_btn = icon_button(self.version_bar, self, "history", "Earlier copies (saved before each change)",
                                       self._show_history)
        self.history_btn.pack(side="right", padx=(0, 6))
        self.export_btn = icon_button(self.version_bar, self, "export", "Export markers to a video editor",
                                      self._export_markers_menu)
        self._speakers_busy = False
        self.version_info = ttk.Label(self.version_bar, style="Muted.TLabel")
        self.version_info.pack(side="left", padx=10)
        self.saved_version: dict[str, float] = {}   # "name|track" -> chosen version's mtime

        sb = ttk.Scrollbar(self.right, command=self.text.yview)
        self.text_sb = sb
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", pady=6)
        self.text.pack(fill="both", expand=True, padx=(6, 0), pady=6)
        self.text.tag_configure("ts", font=("Consolas", 9))
        self.text.tag_configure("info", font=ui_font(9, "italic"))
        self.text.tag_configure("trackhead", font=ui_font(10, bold=True), spacing1=8)
        # Click a timestamp to jump the preview player there.
        # Rows light up instantly under the mouse; clicking a row (not dragging) jumps the player there.
        self._hover_line = None
        self._press_index = None
        self.text.bind("<Motion>", self._on_text_motion)
        self.text.bind("<Leave>", lambda e: self._set_hover(None))
        self.text.bind("<ButtonPress-1>", self._on_text_press, add="+")
        self.text.bind("<ButtonRelease-1>", self._on_row_click, add="+")
        self.text.tag_bind("who", "<Button-1>", self._on_who_click)
        # Paragraph view: the arrow folds a paragraph open or closed.
        self.text.tag_configure("grp", foreground="#8a8a8a", font=ui_font(10))
        self.text.tag_configure("grpsp", font=ui_font(10))
        self._hang_tags: set = set()
        self.text.tag_bind("grp", "<Button-1>", self._on_paragraph_click)
        self.text.tag_bind("grp", "<Enter>", lambda e: self.text.configure(cursor="hand2"))
        self._edit_setup()      # double-click a row to fix its wording
        self.text.bind(RIGHT_CLICK, self._on_row_menu)     # right-click a row
        self.text.bind("<Up>", lambda e: self._key_move(-1))
        self.text.bind("<Down>", lambda e: self._key_move(1))
        self.text.bind("<Prior>", lambda e: self._key_move(-10))
        self.text.bind("<Next>", lambda e: self._key_move(10))
        self.text.bind("<Return>", lambda e: self._key_enter())
        self.bind("<space>", self._space_key)      # space plays/pauses (not while typing)
        # A clicked button keeps the keyboard focus, and space would press it again (Timeline, Snap,
        # Fit...): in the main window space plays/pauses instead; in dialogs it presses as usual.
        for cls in ("TButton", "TCheckbutton", "TRadiobutton", "Button", "Checkbutton", "Radiobutton"):
            self.bind_class(cls, "<space>", self._space_on_button)
        self._find_setup()      # Ctrl+F
        self._find_theme()
        self._markers_setup()   # M marks a row
        self.text.bind("<Motion>", self._suspect_hover, add="+")
        # A right-aligned tab stop at the right edge, for the track name at the end of each row.
        self.text.bind("<Configure>", lambda e: self.text.configure(
            tabs=(max(e.width - 2 * int(self.text.cget("padx")) - 6, 50), "right")), add="+")
        self.text.bind("<Configure>", self._paragraphs_resized, add="+")

    def _build_queue_tab(self):
        q = ttk.Frame(self.tabs)
        self.tabs.add(q, text="Queue")
        self.tree = ttk.Treeview(q, columns=("status", "length"), show="tree headings", selectmode="extended", height=3)
        self.tree.heading("#0", text="File")
        self.tree.heading("status", text="Status")
        self.tree.heading("length", text="Length")
        self.tree.column("#0", width=self.thumb_w + 200)
        self.tree.column("status", width=100, anchor="center")
        self.tree.column("length", width=62, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=6, pady=(8, 0))
        self.tree.bind("<<TreeviewSelect>>", self._on_queue_select)
        self.tree.bind("<ButtonRelease-1>", lambda e: self._open_on_click(e, self.tree), add="+")
        self.tree.bind("<Double-1>", self._open_selected_output)
        self.tree.bind(RIGHT_CLICK, self._queue_menu)

        hint = "Drag files here, or use Add files." if HAS_DND else "Use Add files to choose videos or audio."
        # (the hint and buttons are packed from the bottom first, so a short panel still shows them)
        q_hint = ttk.Label(q, text=hint, style="Muted.TLabel")
        q_hint.pack(side="bottom", anchor="w", padx=6, pady=(4, 0), before=self.tree)
        qb = ttk.Frame(q)
        qb.pack(side="bottom", fill="x", padx=6, pady=6, before=q_hint)
        ttk.Button(qb, text="Add files...", command=self._add_files_dialog).pack(side="left")
        ttk.Button(qb, text="Remove", command=self._remove_selected).pack(side="left", padx=4)
        ttk.Button(qb, text="Clear finished", command=self._clear_finished).pack(side="left")
        rec = ttk.Button(qb, text="\u25cf Record...", command=self._open_recorder)
        rec.pack(side="left", padx=(8, 0))
        Tooltip(rec, "Record your mic and/or PC audio (a stream, a call) and transcribe it")

        self.queue_frame = q
        self.drop_canvas = None
        if HAS_DND:
            for w in (self.tree, q):
                self._make_drop_target(w)

    def _build_saved_tab(self):
        t = ttk.Frame(self.tabs)
        self.tabs.add(t, text="Library")
        self.saved_frame = t
        # Searching every transcript: Ctrl+F on this tab (the find box, see finder.py) sets search_var.
        self._search_placeholder = False
        self.search_var.trace_add("write", lambda *_: self._search_changed())

        self.saved = ttk.Treeview(t, columns=("versions", "date"), show="tree headings", selectmode="browse", height=3)
        self.saved.heading("#0", text="Transcript")
        self.saved.heading("versions", text="Versions")
        self.saved.heading("date", text="Latest")
        self.saved.column("#0", width=self.thumb_w + 180)
        self.saved.column("versions", width=78, anchor="center")
        self.saved.column("date", width=132, anchor="center")
        self.saved.pack(fill="both", expand=True, padx=6, pady=(8, 0))
        self.saved.bind("<<TreeviewSelect>>", lambda e: self._on_saved_select())
        self.saved.bind("<ButtonRelease-1>", lambda e: self._open_on_click(e, self.saved), add="+")
        self.saved.bind("<Double-1>", lambda e: self._open_saved())
        if HAS_DND:
            self._make_drop_target(self.saved)

        s_count = ttk.Label(t, textvariable=self.saved_count_var, style="Muted.TLabel")
        s_count.pack(side="bottom", anchor="w", padx=6, pady=(4, 0), before=self.saved)
        sb = ttk.Frame(t)
        sb.pack(side="bottom", fill="x", padx=6, pady=6, before=s_count)
        ttk.Button(sb, text="Open", command=self._open_saved).pack(side="left")
        icon_button(sb, self, "folder", "Show in folder", self._show_saved_in_folder).pack(side="left", padx=4)
        icon_button(sb, self, "refresh", "Refresh the list", lambda: self._refresh_saved(force=True)).pack(side="left")
        icon_button(sb, self, "delete", "Delete transcript (all versions)", self._delete_saved).pack(side="right")
        self.saved.bind("<Delete>", lambda e: self._delete_saved())
        self.saved.bind(RIGHT_CLICK, self._saved_menu)

    # ------------------------------------------------------------------ settings dialog

    def _update_summary(self):
        fmts = [n for f, n in (("txt", "Text"), ("srt", "SRT"), ("vtt", "VTT")) if self.fmt_vars[f].get()]
        self.summary_var.set(f"·  {self.lang_var.get()}  ·  "
                             f"{' + '.join(fmts) or 'no format'}")

    def _show_model_menu(self, _event=None):
        """A small themed dropdown under the model chip (native Tk menus ignore dark mode on Windows)."""
        if self.model_popup is not None:
            self._close_model_menu()
            return
        mode = self.current_theme or "light"
        p = PALETTE[mode]
        bg, hover, border = ("#ffffff", "#e8f1fb", "#d0d0d0") if mode == "light" else ("#2b2b2b", "#383838", "#454545")
        accent = DROP_ACCENT[mode]

        pop = tk.Toplevel(self)
        pop.overrideredirect(True)
        pop.configure(background=border, padx=1, pady=1)
        self.model_popup = pop
        box = tk.Frame(pop, background=bg, padx=4, pady=4)
        box.pack(fill="both", expand=True)

        current = self.model_var.get()
        for m in MODELS:
            row = tk.Frame(box, background=bg, cursor="hand2")
            row.pack(fill="x")
            check = tk.Label(row, text="\u2713" if m == current else "", width=2, background=bg,
                             foreground=accent, font=ui_font(10, bold=True))
            name = tk.Label(row, text=m, width=8, anchor="w", background=bg, foreground=p["fg"],
                            font=ui_font(10, bold=m == current))
            note = tk.Label(row, text=MODEL_NOTES.get(m, ""), anchor="w", background=bg,
                            foreground=p["muted"], font=ui_font(9))
            check.pack(side="left", padx=(4, 0), pady=4)
            name.pack(side="left")
            note.pack(side="left", padx=(6, 14), fill="x", expand=True)
            parts = (row, check, name, note)

            def paint(color, parts=parts):
                for w in parts:
                    w.configure(background=color)
            for w in parts:
                w.bind("<Enter>", lambda e, paint=paint: paint(hover))
                w.bind("<Leave>", lambda e, paint=paint: paint(bg))
                w.bind("<Button-1>", lambda e, m=m: self._pick_model(m))

        pop.update_idletasks()
        chip = self.model_chip
        x = chip.winfo_rootx() + chip.winfo_width() - pop.winfo_reqwidth()   # right-aligned under the chip
        y = chip.winfo_rooty() + chip.winfo_height() + 4
        pop.geometry(f"+{x}+{y}")   # negative is fine: monitors left of the main one have negative x
        pop.bind("<Escape>", lambda e: self._close_model_menu())
        pop.focus_force()
        # Close when clicking anywhere else in the app or switching away.
        self._popup_click = self.bind_all("<Button-1>", self._maybe_close_model_menu, add="+")
        pop.bind("<FocusOut>", lambda e: self.after(150, self._close_if_unfocused))

    def _maybe_close_model_menu(self, event):
        pop = self.model_popup
        if pop is None:
            return
        w = event.widget
        try:
            inside = str(w).startswith(str(pop)) or w == self.model_chip
        except Exception:
            inside = False
        if not inside:
            self._close_model_menu()

    def _close_if_unfocused(self):
        pop = self.model_popup
        if pop is not None and pop.winfo_exists():
            f = self.focus_get()
            if f is None or not str(f).startswith(str(pop)):
                self._close_model_menu()

    def _close_model_menu(self):
        if self.model_popup is not None:
            try:
                self.model_popup.destroy()
            except tk.TclError:
                pass
            self.model_popup = None
            self.unbind_all("<Button-1>")
            self.model_chip.configure(style="Chip.TLabel")

    def _pick_model(self, model):
        self._close_model_menu()
        if model != self.model_var.get():
            self.model_var.set(model)
            self._model_changed()

    def _model_changed(self):
        """Save the choice and load the new model in the background so Transcribe stays instant."""
        save_settings(self._settings())
        model = self.model_var.get()
        if model == "parakeet":
            from transcriber import setup_check
            if not setup_check._nvidia_component()["ok"]:
                self.status_var.set("Parakeet needs the NVIDIA models: install them here.")
                self.after(100, lambda: self._open_setup("nvidia"))
                return
        if is_model_loaded(model):
            self.status_var.set(f"Model set to '{model}'.")
            return
        if self.worker and self.worker.is_alive():
            self.status_var.set(f"Model set to '{model}'. It will be used for the next transcription.")
            return
        self.status_var.set(f"Loading the '{model}' model...")

        def load():
            try:
                from transcriber.core import load_model
                load_model(model, log=lambda m: None)
                self.events.put(("model_ready", model, None))
            except Exception as exc:
                self.events.put(("model_ready", model, str(exc)))
        threading.Thread(target=load, daemon=True).start()

    def _redisplay(self):
        """Show the current selection again (after names, photos or the picture shape change)."""
        if self.display and self.display[0] == "saved":
            self._show_saved(self.display[1])
        elif self.display and self.display[0] == "job":
            job = self._job(self.display[1])
            if job:
                self._show_job(job)

    # ------------------------------------------------------------------ right-hand transcript panel

    def _band_line(self, line: int):
        """Shade every other timestamped line (only lines that start with a timestamp count),
        and remember each row's start time so the player position can be shown."""
        if "ts" in self.text.tag_names(f"{line}.0"):
            m = re.match(r"\[(\d+):(\d+):(\d+(?:\.\d+)?)[\]–]", self.text.get(f"{line}.0", f"{line}.16"))
            if m:
                self._rows.append((int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]), line))
            self._band_n += 1
            if self._band_n % 2 == 0:
                self.text.tag_add("band", f"{line}.0", self._row_end(line))

    def _insert(self, text, tag=None):
        self.text.configure(state="normal")
        self.text.insert("end", text, tag)
        if text.endswith("\n"):
            self._band_line(int(self.text.index("end-1c").split(".")[0]) - 1)
        self.text.see("end")
        self.text.configure(state="disabled")

    TRANSCRIPT_MAX = 760     # widest the transcript gets; past that, the player side grows

    def _left_width(self, total: int) -> int:
        """Width of the list/player side: where you dragged the divider, else automatic (wide window:
        the extra width goes to the list and player instead of very long lines)."""
        left_min = int(320 * self.ui_scale)
        right_min = int(360 * self.ui_scale)
        sash = int(10 * self.ui_scale)
        if self.left_width:
            want = int(self.left_width * self.ui_scale)
        else:
            want = max(int(450 * self.ui_scale), total - int(self.TRANSCRIPT_MAX * self.ui_scale) - sash)
        return max(left_min, min(want, total - right_min - sash))

    def _on_body_resize(self, e):
        left = self._left_width(e.width)
        if abs(int(self._body.grid_columnconfigure(0)["minsize"]) - left) > 4:
            self._body.columnconfigure(0, minsize=left)
        self._fit_preview(e.height)

    def _fit_preview(self, height=None):
        """The picture gets the column's height, less the captions and controls under it and the
        timeline when it's open."""
        p = self.player
        height = height or self._body.winfo_height()
        self.update_idletasks()
        below = (p.caption.winfo_reqheight() if p.caption.winfo_manager() else 0) + p._controls.winfo_reqheight()
        holder = getattr(self, "_tl_holder", None)
        if holder is not None and holder.winfo_manager() and getattr(self, "timeline_open", False):
            below += holder.winfo_reqheight()
        room = height - below - int(56 * self.ui_scale)
        if abs(room - p.max_screen_h) > 2:
            p.max_screen_h = max(room, 120)
            p.after_idle(p.fit_screen)

    # ---- the divider between the list/player side and the transcript

    # ---- the right side's tabs

    def _ts(self, sec: float) -> str:
        """A row's timestamp: 00:01:23, or 00:01:23.456 with milliseconds turned on."""
        return format_timestamp(sec, millis=True, sep=".") if getattr(self, "ts_millis", False) else format_timestamp(sec)

    def _set_ts_millis(self, on: bool):
        self.ts_millis = bool(on)
        self._pcol = None                          # (paragraph columns are measured from the timestamp)
        self._name_w = {}
        self._hang_tags = set()
        save_settings(self._settings())
        self._rerender_in_place()

    def _paint_trash(self):
        """The delete button's trash can: the icon font's when Windows has it, else drawn."""
        from transcriber.icons import icon_family, trash_image
        if icon_family(self) or not hasattr(self, "version_del_btn"):
            return
        try:
            self._trash_img = trash_image(self._palette()["fg"], self.ui_scale)
            self.version_del_btn.configure(image=self._trash_img, text="", width=0)
        except Exception:
            self.version_del_btn.configure(text="Delete")

    def _show_tab(self, name: str):
        """Switch to "transcript", "queue" or "library"."""
        page = {"transcript": self.right, "queue": self.queue_frame, "library": self.saved_frame}[name]
        if str(self.tabs.select()) != str(page):
            self.tabs.select(page)

    def _tab_name(self) -> str:
        current = str(self.tabs.select())
        return "queue" if current == str(self.queue_frame) else "library" if current == str(self.saved_frame) \
            else "transcript"

    def _set_title(self, title: str):
        self.title_label.configure(text=title or "")

    def _update_queue_tab(self):
        """'Queue (2)' while files are waiting or being transcribed, so it shows from any tab."""
        try:
            n = sum(1 for j in self.jobs if j["status"] in (PENDING, WORKING))
            self.tabs.tab(self.queue_frame, text=f"Queue ({n})" if n else "Queue")
        except (tk.TclError, AttributeError):
            pass

    def _open_on_click(self, e, tree):
        """A plain click on a file in Queue or Library opens it in the Transcript tab."""
        if e.state & 0x0005 or not tree.identify_row(e.y) or tree.identify_region(e.x, e.y) not in ("tree", "cell"):
            return
        self.after_idle(lambda: self._show_tab("transcript"))

    def _sash_press(self, e):
        self._sash_drag = (e.x_root, int(self._body.grid_columnconfigure(0)["minsize"]))
        self._sash_line.configure(background=DROP_ACCENT[self.current_theme or "light"])
        self._sash_line.place(relx=0.5, rely=0.5, anchor="center", relheight=0.12, width=3)

    def _sash_move(self, e):
        if not self._sash_drag:
            return
        x0, w0 = self._sash_drag
        self.left_width = max(1, int((w0 + e.x_root - x0) / self.ui_scale))
        left = self._left_width(self._body.winfo_width())
        self._body.columnconfigure(0, minsize=left)

    def _sash_release(self, e):
        self._sash_drag = None
        self._sash_theme()
        self._sash_line.place_forget()
        self.left_width = int(int(self._body.grid_columnconfigure(0)["minsize"]) / self.ui_scale)
        self._save_settings_quietly()

    def _sash_reset(self, e):
        """Double-click the divider: back to the automatic width."""
        self.left_width = 0
        self._body.columnconfigure(0, minsize=self._left_width(self._body.winfo_width()))
        self._save_settings_quietly()

    def _sash_theme(self):
        p = self._palette()
        bg = ttk.Style(self).lookup("TFrame", "background") or p["bg"]
        self._sash.configure(background=bg)
        self._sash_line.configure(background=p["muted"])

    def _busy(self, on: bool, what: str = ""):
        """Show (or hide) the small working indicator next to the status text."""
        if on:
            self._busy_jobs.append(what)
        elif self._busy_jobs:
            self._busy_jobs.pop(0)
        if self._busy_jobs:
            n = len(self._busy_jobs)
            self._busy_lbl.configure(text=self._busy_jobs[-1] + (f" (+{n - 1} more)" if n > 1 else ""))
            if not self._busy_box.winfo_ismapped():
                self._busy_box.pack(side="right", padx=(8, 8), after=self.start_btn)
                self._busy_bar.start(12)
        elif self._busy_box.winfo_ismapped():
            self._busy_bar.stop()
            self._busy_box.pack_forget()

    def _lang_code(self):
        return dict(LANGUAGES).get(self.lang_var.get(), "") or None

    def _vocab_prompt(self, person_name: str = "") -> Optional[str]:
        """Names and terms for Whisper: your shared list, everyone's names, and the words listed
        for each person (only that person's when it's their own mic)."""
        from transcriber.core import split_words, vocab_prompt
        words = split_words(self.vocab)
        people = list(self.people.people)
        mine = self.people.by_name(person_name) if person_name else None
        words += [p["name"] for p in people]
        for p in ([mine] if mine else people):
            words += split_words(p.get("words", ""))
        return vocab_prompt(words)

    def _save_settings_quietly(self):
        try:
            save_settings(self._settings())
        except Exception:
            pass

    def _on_text_press(self, e):
        self._press_index = self.text.index(f"@{e.x},{e.y}")
        if self._editing is None:
            self.text.focus_set()     # so the arrow keys move through the rows
        if self._editing is not None and int(self._press_index.split(".")[0]) != self._editing:
            self._end_edit()      # clicking another row finishes the edit

    def _render(self, title: str, entries, scroll_end: bool = True, versions: bool = False):
        self._edit_before_render()
        editable = {}
        self._set_title(title)
        if versions and not self.version_bar.winfo_ismapped():
            self.version_bar.pack(fill="x", padx=8, pady=(8, 0), before=self.text_sb)
        elif not versions and self.version_bar.winfo_ismapped():
            self.version_bar.pack_forget()
        self._hover_line = None
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self._who_by_line = {}
        self._group_lines = {}           # paragraph view: line -> {"key", "open", "metas"}
        self._hidden_rows = []           # the lines inside folded paragraphs (not drawn on their own)
        self._group_keys = []
        for entry in entries:
            if entry[0] == "\x00grp":           # a paragraph starts on this line
                info = entry[1]
                self._group_lines[int(self.text.index("end-1c").split(".")[0])] = info
                self._group_keys.append(info["key"])
                if not info["open"]:
                    self._hidden_rows += info["metas"]
                continue
            if entry[0] == "\x00img":            # ("\x00img", image, tags): an inline picture
                self.text.image_create("end", image=entry[1], padx=1)
                if entry[2]:
                    for t in entry[2]:
                        self.text.tag_add(t, "end-2c")
            else:
                if len(entry) > 3 and entry[3]:   # who's speaking on this line (for "Who is this?")
                    self._who_by_line[int(self.text.index("end-1c").split(".")[0])] = entry[3]
                if len(entry) > 4 and entry[4]:   # an editable line of text (double-click to edit)
                    text = self._edit_text_for(entry[4])
                    line, col = (int(x) for x in self.text.index("end-1c").split("."))
                    self.text.insert("end", text, entry[1])
                    editable[line] = (entry[4], col, col + len(text))
                    continue
                self.text.insert("end", entry[0], entry[1])
        self._style_paragraphs()
        self._band_n = 0
        self._rows = []
        self._now_line = None
        # Every other row shaded, and each row's time noted: from the text in one go (one call), not
        # three Tk calls per row, which was most of the time a long transcript took to draw.
        total = int(self.text.index("end-1c").split(".")[0])
        content = self.text.get("1.0", "end-1c").split("\n")
        ts_re = re.compile(r"\[(\d+):(\d+):(\d+(?:\.\d+)?)[\]\u2013]")
        tsend_lines = {ln + 1 for ln, info in self._group_lines.items() if not info["open"] and info.get("span") == 2}
        for line in range(1, total):
            if line in tsend_lines:
                continue                                    # a folded paragraph's second line
            m = ts_re.match(content[line - 1]) if line - 1 < len(content) else None
            if not m:
                continue
            self._rows.append((int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]), line))
            self._band_n += 1
            if self._band_n % 2 == 0:
                self.text.tag_add("band", f"{line}.0", self._row_end(line))
        self.text.see("end" if scroll_end else "1.0")
        self.text.configure(state="disabled")
        self._edit_after_render(editable)
        self._apply_muted_rows()
        self._markers_after_render()
        self._suspects_after_render()
        self._find_after_render()
        self._timeline_refresh()


    def _general_append(self, text, tag=None):
        self.general_log.append((text, tag))
        if self.display is None:
            self._insert(text, tag)

    def _row_at(self, x: int, y: int):
        """The logical line under the mouse if it's a timestamped row, else None."""
        line = int(self.text.index(f"@{x},{y}").split(".")[0])
        if "tsend" in self.text.tag_names(f"{line}.0"):    # a folded paragraph's second line
            line -= 1
        if "ts" not in self.text.tag_names(f"{line}.0"):
            return None
        last = self.text.dlineinfo(self._row_end(line) + " -1c")   # last screen line of a wrapped row
        if last and y > last[1] + last[3]:
            return None                                   # below the final row
        return line

    def _set_hover(self, line):
        if line == self._hover_line:
            return
        self.text.tag_remove("hover", "1.0", "end")
        self._hover_line = line
        if line is not None:
            self.text.tag_add("hover", f"{line}.0", self._row_end(line))
        self.text.configure(cursor="xterm" if line is not None and line == self._editing else
                            "hand2" if line is not None else "")

    def _on_text_motion(self, event):
        self._set_hover(self._row_at(event.x, event.y))

    def _set_now(self, line):
        if line == self._now_line:
            return
        self.text.tag_remove("now", "1.0", "end")
        self._now_line = line
        if line is not None:
            self.text.tag_add("now", f"{line}.0", self._row_end(line))

    def _on_player_time(self, t, playing: bool):
        """Called by the player a few times a second: move the blue row to where playback is."""
        rows = self._rows
        muted = getattr(self, "_muted_lines", None)
        if muted:
            rows = [r for r in rows if r[1] not in muted]
        if t is None or not rows:
            self._set_now(None)
            return
        if getattr(self, "_tl", None) is not None and self.timeline_open:
            self._tl.set_time(t, playing)
        i = bisect.bisect_right([r[0] for r in rows], t) - 1
        line = rows[max(i, 0)][1]
        if line != self._now_line:
            self._set_now(line)
            if playing:
                self.text.see(f"{line}.0")   # keep the playing row in view

    def _on_row_click(self, event):
        idx = self.text.index(f"@{event.x},{event.y}")
        if self._press_index != idx or self.text.tag_ranges("sel"):
            return                          # it was a drag to select text, not a click
        if "who" in self.text.tag_names(idx) or "grp" in self.text.tag_names(idx):
            return                          # a name opens "Who is this?"; the arrow folds a paragraph
        line = self._row_at(event.x, event.y)
        if self.display and self.display[0] == "search":
            hit = getattr(self, "_search_hits", {}).get(int(idx.split(".")[0]))
            if hit:
                self._jump_to_hit(*hit)
            return
        if line is None or line == self._editing:
            return
        if self._seek_word(line, idx):
            return
        self._seek_line(line)

    def _seek_word(self, line: int, idx: str) -> bool:
        """Clicked on a word whose time is known: play from that word."""
        from transcriber.words import word_at
        meta = self._row_meta.get(line)
        if not meta or not meta["files"].get("words") or meta["key"] in self._edits:
            return False
        t = self.text
        if t.compare(idx, "<", f"b{line}s") or t.compare(idx, ">=", f"b{line}e"):
            return False
        lines = self._words_for(meta["files"]["words"])
        if not lines or meta["i"] >= len(lines) or not lines[meta["i"]]:
            return False
        w = word_at(lines[meta["i"]], meta["orig"], len(t.get(f"b{line}s", idx)))
        if not w:
            return False
        self.player.seek(max(w[0] - 0.05, 0))
        self._set_now(line)
        return True

    def _words_for(self, path):
        """Word timings for a version, cached until the file changes."""
        from transcriber.words import read_words
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        cache = self.__dict__.setdefault("_words_cache", {})
        hit = cache.get(str(path))
        if not hit or hit[0] != mtime:
            hit = cache[str(path)] = (mtime, read_words(path))
        return hit[1]

    def _seek_line(self, line: int):
        m = re.match(r"\[(\d+):(\d+):(\d+(?:\.\d+)?)[\]–]", self.text.get(f"{line}.0", f"{line}.0 lineend"))
        if m:
            meta = self._row_meta.get(line)              # (the exact time, not the one rounded for showing)
            sec = float(self._shown_time(meta)) if meta and "sec" in meta else \
                int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
            self.player.seek(sec)
            self._set_now(line)   # don't wait for the next player update
            self._timeline_row_clicked(line)

    def _key_move(self, step: int):
        """Up/Down arrows move the highlighted row; Enter plays from it."""
        if self._editing is not None or getattr(self, "_time_entry", None):
            return "break"
        lines = [line for _, line in self._rows]
        if not lines:
            return "break"
        cur = self._hover_line if self._hover_line in lines else self._now_line
        if cur in lines:
            i = max(0, min(len(lines) - 1, lines.index(cur) + step))
        else:                               # start from the first row on screen
            top = int(self.text.index("@0,0").split(".")[0])
            i = next((k for k, line in enumerate(lines) if line >= top), 0)
        self._set_hover(lines[i])
        self.text.see(f"{lines[i]}.0")
        return "break"

    def _space_key(self, e):
        """Space bar: play/pause the preview, unless it's typing (editing a row or a time, a text box)."""
        if self._editing is not None or getattr(self, "_time_entry", None):
            return None
        w = e.widget
        if isinstance(w, (tk.Entry, ttk.Entry, tk.Spinbox, tk.Listbox)):
            return None
        if isinstance(w, tk.Text) and w is not self.text:
            return None
        self.player.toggle()
        return "break"

    def _space_on_button(self, e):
        w = e.widget
        try:
            main = w.winfo_toplevel() is self
        except (tk.TclError, AttributeError):
            main = False
        if main and self._editing is None and not getattr(self, "_time_entry", None):
            self.player.toggle()
            return "break"
        try:
            if str(w.cget("state")) != "disabled":
                w.invoke()
        except (tk.TclError, AttributeError):
            pass
        return "break"

    def _key_enter(self):
        if self._editing is not None:      # Enter while editing a line finishes the edit
            self._end_edit()
            return "break"
        if self._editing is None and self._hover_line is not None and self._hover_line in [l for _, l in self._rows]:
            self._seek_line(self._hover_line)
        return "break"

    def _rows_entries(self, rows, files, track: str = "", track_i: int = 0, indices=None, glyph=None):
        """Transcript rows as display entries: time, then (if known) a photo and name, then the text,
        and (when several tracks are shown together) the track's name at the right end.
        glyph (paragraph view): None, "single", "inner" (a line of an opened paragraph) or a
        paragraph info dict (its first line, with the fold-up arrow)."""
        size = max(int(18 * self.ui_scale), 14)
        entries = []
        for n, (sec, label, name, text, pid) in enumerate(rows):
            meta = {"files": files, "i": indices[n] if indices else n, "orig": text, "track": track, "sec": sec}
            person = self.people.get(pid)
            if glyph is None:
                entries.append((f"[{self._ts(sec)}]  ", "ts"))
            else:
                # Paragraph view: every row in the same columns (time, arrow, photo), and the text
                # (wrapped lines and an opened paragraph's lines too) lined up where it starts.
                if isinstance(glyph, dict):
                    entries.append(("\x00grp", glyph))
                lead = glyph.get("lead") if isinstance(glyph, dict) else None
                if glyph == "inner":
                    lead = getattr(self, "_inner_lead", None) or self._paragraph_col()
                elif lead is None:
                    lead = self._text_start(name)
                    if isinstance(glyph, dict):
                        glyph["lead"] = lead
                self._inner_lead = lead
                entries.append((f"[{self._ts(sec)}]", ("ts", self._hang_tag(lead))))
                if isinstance(glyph, dict):
                    entries.append(("  \u25be", ("grp",)))
                entries.append(("\t\t" if glyph == "inner" and lead > self._paragraph_col() else "\t", None))
            if name and glyph != "inner":        # (inside an opened paragraph the speaker is already shown)
                color = person["color"] if person else "#64748b"
                tag = self._color_tag(color)
                img = self.avatars.get(person, size, name, color)
                who = (files, label)
                entries.append(("\x00img", img, ("who",)))
                entries.append((f" {name}:  ", (tag, "who")))
                entries.append((text, None, None, who, meta))
            elif name:
                entries.append((text, None, None, (files, label), meta))
            else:
                entries.append((text, None, None, None, meta))
            if track and track != name:        # (a person's own mic: the name already says it)
                entries.append((f"\t{track}", f"trk{track_i % 4}"))
            entries.append(("\n", None))
        return entries

    GROUP_GAP = 2.0           # a longer pause starts a new paragraph
    GROUP_MAX_SECS = 45.0
    GROUP_MAX_CHARS = 450

    def _view_entries(self, records) -> list:
        """The transcript's rows for the panel, line by line or as paragraphs (Lines / Paragraphs).
        records = [(row, files, track name shown, track index, row index in its file)]."""
        if self.transcript_view != "paragraphs":
            out = []
            for r, files, track, ti, k in records:
                out += self._rows_entries([r], files, track, ti, [k])
            return out
        out = []
        for g in self._paragraphs(records):
            recs = g["recs"]
            if len(recs) == 1:
                r, files, track, ti, k = recs[0]
                out += self._rows_entries([r], files, track, ti, [k], glyph="single")
                continue
            metas = []
            for r, files, track, ti, k in recs:
                meta = {"files": files, "i": k, "orig": r[3], "track": track, "sec": r[0]}
                self._edit_text_for(meta)
                metas.append(meta)
            changed = any(m["key"] in self._edits or self._time_key(m) in self._edits for m in metas)
            info = {"key": g["key"], "open": g["key"] in self._expanded or changed, "metas": metas}
            if info["open"]:
                for n, (r, files, track, ti, k) in enumerate(recs):
                    out += self._rows_entries([r], files, track, ti, [k], glyph=info if n == 0 else "inner")
                continue
            out += self._paragraph_entries(g, info)
        return out

    def _paragraphs(self, records) -> list:
        """Consecutive lines of the same speaker (and track) with no long pause, as paragraphs:
        [{"key", "recs", "end"}]."""
        groups, cur = [], None
        for rec in records:
            r, files, track, ti, k = rec
            words = self._words_for(files["words"]) if files.get("words") else None
            ws = words[k] if words and k < len(words) and words[k] else None
            end = float(ws[-1][1]) if ws else float(r[0]) + max(1.0, len(r[3]) / 14)
            if cur is not None:
                pr = cur["recs"][-1]
                same = (pr[0][2] or "") == (r[2] or "") and pr[0][4] == r[4] and pr[2] == track and pr[1] is files
                if (same and float(r[0]) - cur["end"] < self.GROUP_GAP
                        and float(r[0]) - float(cur["recs"][0][0][0]) < self.GROUP_MAX_SECS
                        and cur["chars"] + len(r[3]) < self.GROUP_MAX_CHARS):
                    cur["recs"].append(rec)
                    cur["end"] = max(cur["end"], end)
                    cur["chars"] += len(r[3]) + 1
                    continue
            cur = {"key": (str(self._file_key(files)), k), "recs": [rec], "end": end, "chars": len(r[3])}
            groups.append(cur)
        return groups

    def _paragraph_col(self) -> int:
        """Where the photos start in paragraph view (pixels): right of the times and the arrow."""
        hit = getattr(self, "_pcol", None)
        if hit is None:
            from tkinter import font as tkfont
            ts = tkfont.Font(font=("Consolas", 9)).measure(f"[{self._ts(0)}]")
            arrow = tkfont.Font(font=ui_font(10)).measure("  \u25b8  ")
            hit = self._pcol = ts + arrow + 4
        return hit

    def _text_width(self, text: str) -> int:
        """About how wide text is in the transcript's font: the letters' widths added up (each
        letter measured once). Asking Tk to measure every word costs a millisecond a word, which is
        seconds for a long transcript; adding letters is off by a pixel or two from kerning, which
        is fine for deciding where a paragraph's first line ends."""
        table = self.__dict__.get("_char_w")
        if table is None:
            from tkinter import font as tkfont
            self._body_font = tkfont.Font(font=self.text.cget("font"))
            table = self._char_w = {}
        font = self._body_font
        total = 0
        for ch in text:
            w = table.get(ch)
            if w is None:
                w = table[ch] = font.measure(ch)
            total += w
        return total

    def _text_start(self, name: str) -> int:
        """Where a row's words start in paragraph view: past the photo and the speaker's name."""
        from tkinter import font as tkfont
        col = self._paragraph_col()
        if not name:
            return col
        cache = self.__dict__.setdefault("_name_w", {})
        if name not in cache:
            cache[name] = tkfont.Font(font=ui_font(10, bold=True)).measure(f" {name}:  ")
        return col + max(int(18 * self.ui_scale), 14) + 2 + cache[name]

    def _hang_tag(self, lead: int) -> str:
        """A tag for a paragraph-view row: tab stops at the photo column and at its text, and
        wrapped lines starting under its text."""
        tag = f"hang{lead}"
        if tag not in self._hang_tags:
            self._hang_tags.add(tag)
            self._configure_hang(tag, lead)
        return tag

    def _configure_hang(self, tag: str, lead: int):
        t = self.text
        col = self._paragraph_col()
        right = max(t.winfo_width() - 2 * int(t.cget("padx")) - 6, lead + 60)
        stops = (col, "left") + ((lead, "left") if lead > col else ()) + (right, "right")
        t.tag_configure(tag, tabs=stops, tabstyle="wordprocessor", lmargin2=lead)

    def _paragraph_entries(self, g, info) -> list:
        """A folded paragraph, two lines tall: the start time with the end time under it, and the
        speaker and text in a column of their own (the text never runs under the times)."""
        recs = g["recs"]
        (sec, label, name, _text, pid), files, track, ti, _k = recs[0]
        size = max(int(18 * self.ui_scale), 14)
        words = " ".join(r[3] for r, *_ in recs).split()
        # how much fits on the first line (the rest goes on the second, next to the end time)
        col = self._paragraph_col()
        width = self.text.winfo_width() if self.text.winfo_width() > 50 else 640
        self._para_w = width
        room = width - 2 * int(self.text.cget("padx")) - col - 12
        if name:
            room -= size + 4 + self._text_start(name) - col - size - 2
        if track and track != name:
            room -= self._text_width(track) + 24
        # how many words fit (widths from the letters, so a little margin for kerning)
        space = self._text_width(" ")
        room -= 6
        n, used = 0, 0
        while n < len(words):
            add = self._text_width(words[n]) + (space if n else 0)
            if used + add > room:
                break
            used += add
            n += 1
        n = max(n, 1) if room > 60 else n
        first, rest = " ".join(words[:n]), " ".join(words[n:])
        info["span"] = 2
        hang = self._hang_tag(self._text_start(name))
        entries = [("\x00grp", info), (f"[{self._ts(sec)}]", ("ts", hang)), ("  \u25b8", ("grp",)),
                   ("\t", None)]
        person = self.people.get(pid)
        if name:
            color = person["color"] if person else "#64748b"
            entries.append(("\x00img", self.avatars.get(person, size, name, color), ("who",)))
            entries.append((f" {name}:  ", (self._color_tag(color), "who"), None, (files, label)))
        entries.append((first, None))
        if track and track != name:          # (room for it was kept on the first line)
            entries.append((f"\t{track}", f"trk{ti % 4}"))
        entries.append(("\n", None))
        entries.append((f"[{self._ts(g['end'])}]", ("tsend", hang)))
        entries.append((("\t\t" if name else "\t") + rest, None))
        entries.append(("\n", None))
        return entries

    def _row_end(self, line: int) -> str:
        """The end of a row in the transcript (a folded paragraph is two lines)."""
        info = getattr(self, "_group_lines", {}).get(line)
        if info and not info["open"] and info.get("span") == 2:
            return f"{line + 1}.0 lineend +1c"
        return f"{line}.0 lineend +1c"

    def _paragraphs_resized(self, e):
        """Paragraphs fill their first line to the panel's width: fit them again after a resize."""
        used = getattr(self, "_para_w", None)          # the width the paragraphs were last fitted to
        if self.transcript_view != "paragraphs" or used is None or abs(e.width - used) < 30:
            return
        self._para_w = e.width
        if getattr(self, "_para_job", None):
            self.after_cancel(self._para_job)
        self._para_job = self.after(250, lambda: (setattr(self, "_para_job", None), self._rerender_in_place()))

    def _style_paragraphs(self):
        """Paragraph view: the rows' tab stops follow the panel's width (the track name sits at the
        right edge), and a folded paragraph's two lines read as one block."""
        t = self.text
        for tag in self._hang_tags:
            self._configure_hang(tag, int(tag[4:]))
            # Tk takes a wrapped line's indent from its own first character, so the whole row
            # carries the tag (it's put on the timestamp when the row is drawn).
            ranges = t.tag_ranges(tag)
            for start in ranges[::2]:
                t.tag_add(tag, f"{start} linestart", f"{start} lineend +1c")
        t.tag_configure("tsend", font=("Consolas", 9), foreground=self._palette()["ts"])
        t.tag_configure("grptop", spacing3=0)
        t.tag_configure("grpbot", spacing1=0)
        for tag in ("grptop", "grpbot"):
            t.tag_remove(tag, "1.0", "end")
        for line, info in getattr(self, "_group_lines", {}).items():
            if not info["open"] and info.get("span") == 2:
                t.tag_add("grptop", f"{line}.0", f"{line}.0 lineend +1c")
                t.tag_add("grpbot", f"{line + 1}.0", f"{line + 1}.0 lineend +1c")

    def _all_row_metas(self) -> list:
        """Every row shown, including the ones inside folded paragraphs."""
        return list(getattr(self, "_row_meta", {}).values()) + list(getattr(self, "_hidden_rows", []))

    def _set_view(self, view: str):
        if view == self.transcript_view:
            return
        self.transcript_view = view
        self.view_var.set(view == "paragraphs")
        save_settings(self._settings())
        self._rerender_in_place()

    def _toggle_paragraph(self, line: int, open_: Optional[bool] = None):
        info = getattr(self, "_group_lines", {}).get(line)
        if not info:
            return
        want = (not info["open"]) if open_ is None else open_
        if want:
            self._expanded.add(info["key"])
        else:
            self._expanded.discard(info["key"])
        self._rerender_in_place(focus_key=info["key"])

    def _expand_all(self, open_: bool):
        if open_:
            self._expanded.update(getattr(self, "_group_keys", []))
        else:
            self._expanded.clear()
        self._rerender_in_place()

    def _rerender_in_place(self, focus_key=None):
        """Draw the transcript again without jumping away from where you were looking."""
        top = self.text.yview()[0]
        self._redisplay()
        self.text.yview_moveto(top)
        if focus_key is not None:
            line = next((ln for ln, info in self._group_lines.items() if info["key"] == focus_key), None)
            if line:
                self.text.see(f"{line}.0")

    def _on_paragraph_click(self, event):
        line = int(self.text.index(f"@{event.x},{event.y}").split(".")[0])
        self._toggle_paragraph(line)
        return "break"

    def _color_tag(self, color: str) -> str:
        tag = "pc" + color.lstrip("#")
        if tag not in self.text.tag_names():
            self.text.tag_configure(tag, foreground=color, font=ui_font(10, bold=True))
        return tag

    # ---- "Who is this?"

    # ---- right-click menu on a row

    # ---- People

    def _show_general(self):
        self.display = None
        self.player.load(None)
        entries = self.general_log or [("Add videos or audio in the Queue tab, then click Transcribe.\n"
                                        "Select any file to see its transcript here.\n", "info")]
        self._render("Transcript", entries)


    TILE_COLORS = ["#79b0ff", "#ffb07a", "#7ddca0", "#d1a3ff"]     # the transcript's track colors (dark)

    def _set_call(self, source, caps, track_order=None):
        """The named speakers, for the player's call view (audio files, or the Audio only view).
        Without speakers, each separately transcribed track (Mic, Discord...) gets a tile instead."""
        tiles, audio_file = [], False
        if source and os.path.exists(source):
            cache = self.__dict__.setdefault("_video_cache", {})
            if source not in cache:
                cache[source] = Path(source).suffix.lower() not in AUDIO_EXTENSIONS and has_video_stream(source)
            audio_file = not cache[source]
            seen = set()
            for cap in caps:
                name = cap[2] if len(cap) > 3 else ""
                if name and name not in seen:
                    seen.add(name)
                    tiles.append({"name": name, "color": cap[3], "person": self.people.by_name(name)})
            if not tiles:
                order = list(track_order or [])
                for cap in caps:
                    if len(cap) > 4 and cap[4] and cap[4] not in order:
                        order.append(cap[4])
                for i, track in enumerate(order):
                    person = self.people.by_name(track)      # a mic named after someone shows their photo
                    tiles.append({"name": track, "track": track, "person": person,
                                  "color": person["color"] if person else self.TILE_COLORS[i % 4]})
        self.player.set_call(tiles, self.avatars.shape, audio_file=audio_file)

    def _cap(self, r) -> tuple:
        """A caption for the player: (start, text, speaker name, name color)."""
        sec, _label, name, text, pid = r
        if not name:
            return (sec, text, "", "")
        person = self.people.get(pid)
        return (sec, text, name, person["color"] if person else "#9aa5b1")

    def _merged_entries(self, parts):
        """One or more tracks' transcripts as one list in time order. With several tracks, each row
        ends with its track's name on the right. Returns (entries, captions)."""
        rows = []
        for i, (name, files) in enumerate(parts):
            for k, r in enumerate(rows_for(files, self.people)):
                rows.append((r, i, name, files, k))
        rows.sort(key=lambda x: (x[0][0], x[1]))
        many = len(parts) > 1
        caps = [self._cap(r) + (name if many else "",) for r, i, name, files, k in rows]   # track: muted ones hide
        entries = self._view_entries([(r, files, name if many else "", i, k) for r, i, name, files, k in rows])
        return entries, caps


    def _refresh_preview_caps(self, now: bool = False):
        """The player's captions again (after an unsaved edit), for the transcript shown. Several
        changes in a row (typing) rebuild them once."""
        if not now:
            if getattr(self, "_pcaps_job", None) is None:
                self._pcaps_job = self.after(250, lambda: self._refresh_preview_caps(now=True))
            return
        self._pcaps_job = None
        v, parts = getattr(self, "_pcaps_v", None), getattr(self, "_pcaps_parts", None)
        if v is None or parts is None or not getattr(self, "display", None) or self.display[0] != "saved":
            return
        v["pcaps"] = self._preview_caps(parts)
        self.player.display_captions = (lambda v=v: v["pcaps"]) if v["pcaps"] else None
        self.player._caption_shown = "redraw"
        self.player.refresh_captions()

    def _set_caption_size(self, pct: int):
        """Caption size picked from the video's right-click menu."""
        self.caption_style = {**(self.caption_style or {}), "size": int(pct)}
        self.player.set_caption_style(self.caption_style)
        save_settings(self._settings())
        self.status_var.set(f"Caption size: {pct}%")

    def _apply_caption_options(self):
        """Settings > Captions choices that the caption files and transcription read."""
        from transcriber import captions as C, core
        from transcriber.player import DEFAULT_CAPTIONS
        style = {**DEFAULT_CAPTIONS, **(self.caption_style or {})}
        C.OPTIONS.update(split=bool(style["split"]), cuts=bool(style["cuts"]),
                         overlap=style.get("overlap") or "layer")
        core.KEEP_SOUNDS = bool(style["sounds"])

    def _preview_caps(self, parts) -> list:
        """Caption-sized pieces for the player, like the exported captions: [(start, text, name,
        name color, track, end)]. parts = [(track name, files)]. Built once for a given state of the
        files and pending edits (it's asked for again on every redraw)."""
        from transcriber import captions as C
        if not C.OPTIONS.get("split", True):
            return []
        edits = getattr(self, "_edits", {}) or {}
        try:
            stamp = tuple((track, str(files.get("speakers") or files.get("txt")),
                           os.path.getmtime(files["speakers"]) if files.get("speakers") else 0,
                           os.path.getmtime(files["words"]) if files.get("words") and os.path.exists(files["words"]) else 0)
                          for track, files in parts)
        except OSError:
            stamp = None
        key = (stamp, tuple(sorted((k, v if not isinstance(v, list) else tuple(v)) for k, v in edits.items())),
               tuple(sorted(C.OPTIONS.items())), tuple((p["id"], p.get("color"), p.get("name")) for p in self.people.people))
        cached = getattr(self, "_pcaps_cache", None)
        if stamp is not None and cached and cached[0] == key:
            self._pcaps_spans = cached[2]
            return cached[1]
        out = self._preview_caps_build(parts)
        if stamp is not None:
            self._pcaps_cache = (key, out, self._pcaps_spans)
        return out

    def _preview_caps_build(self, parts) -> list:
        from transcriber import captions as C
        many = len(parts) > 1
        lines = []
        edits = getattr(self, "_edits", {}) or {}
        tuples = getattr(self, "_pcaps_tuples", None) or {}      # word lists as tuples, kept between builds
        self._pcaps_tuples = fresh = {}
        for track, files in parts:
            words = self._words_for(files["words"]) if files.get("words") else None
            segs, data = self._tl_segments(files) if hasattr(self, "_tl_segments") else ([], None)
            rows = rows_for(files, self.people, data if data and data.get("segments") else None)
            fkey = self._file_key(files)
            for k, (sec, _label, name, text, pid) in enumerate(rows):
                nxt = rows[k + 1][0] if k + 1 < len(rows) else sec + 8
                ws = None
                if words and k < len(words) and words[k]:
                    ws = tuples.get(id(words[k]))
                    if ws is None or ws[0] is not words[k]:
                        ws = (words[k], tuple(tuple(w) for w in words[k]))
                    fresh[id(words[k])] = ws
                    ws = list(ws[1])
                seg = segs[k] if k < len(segs) else {}
                seg_end = float(seg.get("end") or 0)
                end = ws[-1][1] if ws else seg_end if seg_end > sec else max(min(nxt, sec + 8), sec + 0.5)
                fixed = bool(seg.get("fixed"))           # its time was set on the timeline and saved
                if fixed:
                    end = float(seg.get("end", end))
                # unsaved edits show right away: new text, deleted lines, new times (timeline)
                if (fkey, k) in edits:
                    if edits[(fkey, k)] is None:
                        continue
                    text, ws = edits[(fkey, k)], None
                layer = edits.get((fkey, k, "layer"), seg.get("layer")) if (fkey, k, "layer") in edits else seg.get("layer")
                t = edits.get((fkey, k, "t"))
                if t is not None:
                    s2, e2 = (float(t[0]), float(t[1])) if isinstance(t, (tuple, list)) else \
                        (float(t), float(t) + (end - sec))
                    if ws and abs((e2 - s2) - (end - sec)) < 0.02:      # moved as a whole: words move too
                        ws = [(a + s2 - sec, b + s2 - sec, w) for a, b, w in ws]
                    sec, end = s2, e2
                    fixed = isinstance(t, (tuple, list)) or fixed     # (set on the timeline: shown exactly)
                person = self.people.get(pid)
                lines.append(C.Line(sec, end, text, name or "", ws, track if many else "",
                                    (person["color"] if person else "#9aa5b1") if name else "", fixed,
                                    int(layer) if layer is not None else None, src=(fkey, k)))
        self._pcaps_spans = {}
        try:
            cues = C.build(lines, names="off")
        except Exception:
            return []
        # when each line's caption is on screen (it can outlast the line, to be read): the timeline shows it
        spans: dict = {}
        for c in cues:
            for key in c.src:
                a, b = spans.get(key, (c.start, c.end))
                spans[key] = (min(a, c.start), max(b, c.end))
        self._pcaps_spans = spans
        self._pcaps_cues = cues                     # (the caption checks look at these)
        return [(c.start, c.text, c.name, c.color, c.track, c.end, c.layer, tuple(c.src)) for c in cues]

    TRACKS = "\x00tracks"   # the separate-tracks view: every track together, in time order


    SEARCH_HINT = "Search transcripts"

    def _on_tab_changed(self, _event=None):
        name = self._tab_name()
        self._find_tab_changed(name)
        if name == "queue" and self._list_mode != "queue":
            self._list_mode = "queue"
            self._on_queue_select()
        elif name == "library":
            self._refresh_saved()
            if self._list_mode != "library":
                self._list_mode = "library"
                self._on_saved_select()

    # ------------------------------------------------------------------ queue management


    # ---- audio tracks


    # ---- drag and drop, with a vignette "drop zone" while files are dragged over the list


    # ------------------------------------------------------------------ saved transcripts


    # ---- searching inside every transcript


    def _confirm(self, title, heading, body, items, detail, confirm, cancel: str | None = "Cancel") -> bool:
        """A themed yes/no window. Cancel has focus, so pressing Enter by accident is safe."""
        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self)
        win.resizable(False, False)
        result = {"ok": False}
        p = PALETTE[self.current_theme or "light"]

        frame = ttk.Frame(win, padding=(22, 18))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=heading, font=ui_font(12, bold=True), wraplength=420).pack(anchor="w")
        if body:
            ttk.Label(frame, text=body, wraplength=int(520 * self.ui_scale), justify="left").pack(anchor="w", pady=(10, 4))
        shown = items[:8]
        for name in shown:
            ttk.Label(frame, text="\u2022  " + name, style="Muted.TLabel", wraplength=int(520 * self.ui_scale),
                      justify="left").pack(anchor="w", padx=(8, 0))
        if len(items) > len(shown):
            ttk.Label(frame, text=f"   and {len(items) - len(shown)} more", style="Muted.TLabel").pack(anchor="w", padx=(8, 0))
        if detail:
            tk.Label(frame, text=detail, foreground=p["err"] if "permanently" in detail else p["muted"],
                     background=ttk.Style(self).lookup("TFrame", "background") or p["bg"],
                     font=ui_font(9), wraplength=420, justify="left").pack(anchor="w", pady=(12, 0))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(18, 0))

        def done(ok):
            result["ok"] = ok
            win.destroy()

        ok_btn = ttk.Button(buttons, text=confirm, style="Accent.TButton", command=lambda: done(True))
        ok_btn.pack(side="right")
        cancel_btn = None
        if cancel:
            cancel_btn = ttk.Button(buttons, text=cancel, command=lambda: done(False))
            cancel_btn.pack(side="right", padx=(0, 8))
        win.bind("<Escape>", lambda e: done(False))
        win.protocol("WM_DELETE_WINDOW", lambda: done(False))

        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_reqheight()) // 3
        win.geometry(f"+{x}+{y}")
        set_dark_titlebar(win, self.current_theme == "dark")
        win.grab_set()
        (cancel_btn or ok_btn).focus_set()
        self.wait_window(win)
        return result["ok"]


    def _choose_output(self, parent=None):
        d = filedialog.askdirectory(title="Save transcripts to", initialdir=self.out_var.get(), parent=parent or self)
        if d:
            self.out_var.set(os.path.normpath(d))

    def _open_output_dir(self):
        p = Path(self.out_var.get())
        p.mkdir(parents=True, exist_ok=True)
        open_path(p)

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
            "left_width": self.left_width,
            "vocab": self.vocab,
            "auto_update": self.auto_update.get(),
            "last_update_check": self.last_update_check,
            "skip_version": self.skip_version,
            "formats": [f for f, v in self.fmt_vars.items() if v.get()],
            "appearance": self.appearance_var.get(),
            "allow_network_delete": self.allow_network_delete.get(),
            "track_nicknames": self.track_nicknames,
            "detect_speakers": self.detect_speakers.get(),
            "extract_speech": bool(self.extract_speech.get()),
            "hf_token": self.hf_token.get().strip(),
            "track_default": self.track_default,
            "track1_is_mix": self.track1_mix.get(),
            "engine": self.engine_pref,
            "record": dict(getattr(self, "record_prefs", {}) or {}),
            "speaker_model": getattr(self, "speaker_model", "pyannote"),
            "ts_millis": bool(getattr(self, "ts_millis", False)),
            "captions": self.caption_style,
            "transcript_view": self.transcript_view,
            "timeline_open": bool(getattr(self, "timeline_open", False)),
            "timeline_h": int(getattr(self, "timeline_h", 150)),
            "timeline_keys": dict(getattr(self, "timeline_keys", {"select": "s", "snip": "b", "range": "r"})),
            "avatar_shape": self.avatars.shape,
            "window": self._normal_geometry or self.geometry(),
            "zoomed": self.state() == "zoomed",
        }


    def _remember_geometry(self, event):
        if event.widget is self and self.state() == "normal":
            self._normal_geometry = self.geometry()

    def _on_close(self):
        if not self._edits_before_close():
            return
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_NAME, "A transcription is running. Stop it and quit?"):
                return
            self.cancel_flag.set()
        save_settings(self._settings())
        self.player.release()
        from transcriber import crashlog
        crashlog.stop()
        self.destroy()

    def report_callback_exception(self, kind, exc, tb):
        """An error in a button, menu or timer: keep going, but save the details."""
        from transcriber import crashlog
        crashlog.log_error("Error in the window", exc, tb)
        try:
            self.status_var.set(f"Something went wrong: {exc} (saved to the error log: Settings > Error logs)")
        except Exception:
            pass

    def _crash_context(self) -> str:
        """What was going on, for the error log."""
        bits = []
        d = getattr(self, "display", None)
        if d:
            item = self.saved_items.get(d[1]) if d[0] == "saved" and hasattr(self, "saved_items") else None
            bits.append(f"{d[0]}" + (f" '{item.get('key')}'" if item else ""))
        bits.append(f"view={getattr(self, 'transcript_view', '?')}")
        bits.append(f"timeline={'on' if getattr(self, 'timeline_open', False) else 'off'}")
        bits.append(f"unsaved edits={len(getattr(self, '_edits', {}) or {})}")
        if getattr(self, "_editing", None) is not None:
            bits.append("editing a line")
        if getattr(self, "worker", None) and self.worker.is_alive():
            bits.append("transcribing")
        player = getattr(self, "player", None)
        if player is not None and getattr(player, "path", None):
            bits.append(f"player: {os.path.basename(player.path)}"
                        + (" (Audio only)" if getattr(player, "audio_only_view", False) else ""))
        return ", ".join(bits)

    def show_ready(self, problems: list[str]):
        """Called by the loading screen once everything is loaded (or it was skipped)."""
        from transcriber import crashlog
        for msg in problems:
            self._general_append(msg + "\n\n", "err")
        model = self.model_var.get()
        if not problems and is_model_loaded(model):
            self.status_var.set(f"Ready. '{model}' model loaded. "
                                + (f"{self._count(PENDING)} file(s) ready." if self.jobs else "Add files to get started."))
        if crashlog.previous_crash is not None:
            self.status_var.set("The app closed unexpectedly last time. The details were saved "
                                "(Settings > Error logs).")
        self.deiconify()
        if self._start_zoomed:
            self.state("zoomed")
        self.after(5000, self._auto_update_check)
        self.lift()
        self.focus_force()
        set_dark_titlebar(self, self.current_theme == "dark")

    # ------------------------------------------------------------------ background threads
    # Never touch widgets from these; send events to the UI thread instead.


    def _poll_events(self):
        """Apply what the worker threads report. One failing update must never stop the rest, or
        progress, "done" and the Transcribe button would all freeze."""
        try:
            for _ in range(500):
                try:
                    ev = self.events.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle_event(ev)
                except Exception as exc:
                    from transcriber import crashlog
                    crashlog.log_error(f"Updating the window ({ev[0] if ev else '?'})", exc)
                    self.status_var.set(f"Something went wrong updating the window: {exc} (saved to the error log)")
        finally:
            self.after(100, self._poll_events)

    def _handle_event(self, ev):
        kind = ev[0]
        if kind == "begin":
            _, job, n, total = ev
            self._set_status(job, WORKING)
            self.tree.see(job["iid"])
            self.status_var.set(f"Transcribing {n} of {total}: {Path(job['path']).name}")
            if self.follow_live and self._list_mode == "queue":
                self.tree.selection_set(job["iid"])
                self._show_job(job)
        elif kind == "segment":
            _, job, seg, pct = ev
            job["segs"].append((seg.start, seg.text))
            self._job_append(job, f"[{self._ts(seg.start)}]  ", "ts")
            self._job_append(job, seg.text + "\n")
            self.progress.configure(value=pct)
            if job.get("duration"):
                done = min(seg.end / job["duration"] * 100, 100)
                n_total = self.status_var.get().split(":", 1)[0]
                if n_total.startswith("Transcribing"):
                    self.status_var.set(f"{n_total}: {Path(job['path']).name}  ({done:.0f}%)")
        elif kind == "info":
            _, job, msg = ev
            self._job_append(job, msg + "\n", "info")
        elif kind == "meta":
            _, iid, dur = ev
            job = self._job(iid)
            if job:
                job["duration"] = dur
                vals = list(self.tree.item(iid, "values"))
                vals[1] = short_length(dur)
                self.tree.item(iid, values=vals)
        elif kind == "silent":
            _, iid, silent = ev
            job = self._job(iid)
            if job:
                job["silent"], job["silence_checked"] = silent, True
                self._update_job_label(job)
        elif kind == "silence_ready":
            if self.display and self.display[0] == "saved":
                item = self.saved_items.get(self.display[1])
                if item and item.get("source") == ev[1]:
                    self._redisplay()
        elif kind == "start_again":
            self.start_btn.configure(state="normal")
            self._start()
        elif kind == "call":           # run something on the UI thread for a worker
            ev[1]()
        elif kind == "status_text":
            self.status_var.set(ev[1])
        elif kind == "speakers_added":
            _, name, err = ev
            self._speakers_busy = False
            self._busy(False)
            self.status_var.set(f"Couldn't detect speakers in {name}: {err}" if err else
                                f"Speakers added to {name}. Click a name to say who it is.")
            self._refresh_saved(force=True)
            self._redisplay()
        elif kind == "tracks":
            _, iid, tracks = ev
            job = self._job(iid)
            if job:
                job["tracks"] = tracks
                job["tracks_known"] = True
                self._update_job_label(job)
        elif kind == "track_begin":
            _, job, label, many = ev
            job["segs"] = []            # captions follow the track being transcribed
            if many:
                self._job_append(job, f"\n{label}\n", "trackhead")
        elif kind == "thumb":
            _, tree_name, iid, result = ev
            self._set_item_image(tree_name, iid, result)
        elif kind == "status":
            self._set_status(ev[1], ev[2])
        elif kind == "done":
            _, job, outputs, secs, n, total, parts = ev
            job["outputs"], job["parts"] = outputs, parts
            self._set_status(job, DONE)
            self.progress.configure(value=n / total * 100)
            names = ", ".join(p.name for p in outputs)
            self._job_append(job, f"\nSaved {names}  ({int(secs // 60)}m {int(secs % 60)}s)\n", "info")
            self._refresh_saved()
        elif kind == "error":
            _, job, msg = ev
            self._set_status(job, FAILED)
            self._job_append(job, f"Failed: {msg}\n", "err")
        elif kind == "model_ready":
            _, model, err = ev
            if model == self.model_var.get() and not (self.worker and self.worker.is_alive()):
                self.status_var.set(f"Couldn't load '{model}': {err}" if err else f"Ready. '{model}' model loaded.")
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
            if getattr(self, "_start_when_idle", False) and not self.cancel_flag.is_set():
                self._start_when_idle = False          # a recording finished while this was running
                if self._count(PENDING):
                    self.after(300, self._start)


def self_test() -> int:
    """For the installer builds: check that everything the app needs got packaged. Exit code 0 = OK."""
    try:
        import importlib
        for name in ("ctranslate2", "faster_whisper", "PIL", "sv_ttk", "tkinterdnd2", "transcriber.core",
                     "transcriber.files", "transcriber.edits", "transcriber.review_ui", "transcriber.session_ui",
                     "transcriber.addons", "transcriber.setup_ui"):
            importlib.import_module(name)
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return 0
    except Exception:
        import traceback
        traceback.print_exc()
        return 1


def main():
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    # pythonw has no console, so stdout/stderr are None; Whisper's download progress bar needs somewhere to write.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    if sys.platform == "darwin":
        # Apps opened from Finder don't get the Terminal's PATH, so ffmpeg from Homebrew wouldn't be found.
        extra = [d for d in ("/opt/homebrew/bin", "/usr/local/bin") if os.path.isdir(d)]
        os.environ["PATH"] = os.pathsep.join(extra + [os.environ.get("PATH", "")])
    if getattr(sys, "frozen", False):
        # The installed app carries its own ffmpeg next to it.
        here = os.path.dirname(sys.executable)
        for d in (here, os.path.join(here, "ffmpeg"), os.path.join(getattr(sys, "_MEIPASS", here), "ffmpeg")):
            if os.path.isfile(os.path.join(d, "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")):
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                break
    if sys.platform == "win32":
        try:  # crisp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    from transcriber import crashlog
    holder: dict = {}
    crashlog.start(lambda: holder["app"]._crash_context() if "app" in holder else "starting up")
    app = App()
    holder["app"] = app
    app.withdraw()                      # main window stays hidden until everything is loaded
    if len(sys.argv) > 1:  # files dropped onto the launcher or passed on the command line
        app._add_paths(sys.argv[1:])
    LoadingScreen(app)
    app.mainloop()


if __name__ == "__main__":
    main()
