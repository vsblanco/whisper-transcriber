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
from transcriber.icons import icon_button, setup_styles as setup_icon_styles
from transcriber.people_ui import PeopleMixin
from transcriber.search_ui import SearchMixin
from transcriber.session_ui import SessionMixin
from transcriber.markers_ui import MarkersMixin
from transcriber.review_ui import ReviewMixin
from transcriber.setup_ui import SetupMixin
from transcriber.update_ui import UpdateMixin
from transcriber.settings_ui import SettingsMixin
from transcriber.tools_ui import ToolsMixin
from transcriber.tracks_ui import TracksMixin
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
          MarkersMixin, ReviewMixin, SetupMixin, UpdateMixin, BaseTk):
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
        self.hf_token = tk.StringVar(value=s.get("hf_token", ""))
        self.left_width = int(s.get("left_width") or 0)     # 0 = automatic
        self.vocab = s.get("vocab", "")                     # names and terms Whisper should know
        self.auto_update = tk.BooleanVar(value=bool(s.get("auto_update", True)))
        self.last_update_check = float(s.get("last_update_check") or 0)
        self.skip_version = s.get("skip_version", "")
        self.track_default = s.get("track_default", "each")
        self.track1_mix = tk.BooleanVar(value=bool(s.get("track1_is_mix", False)))
        self.engine_pref = s.get("engine", "auto")
        self.caption_style = s.get("captions") or {}
        set_engine(self.engine_pref)
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
        self._search_focus(False)   # show the gray "Search transcripts" hint
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
        self._tab_cache = None   # tab sizes can change with the theme; measure again
        setup_icon_styles(self)
        if hasattr(self, "_sash"):
            self.after_idle(self._sash_theme)
        if getattr(self, "_search_placeholder", False):
            self.search_entry.configure(foreground=p["muted"])
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
        self.tabs = ttk.Notebook(left)
        self.tabs.pack(fill="both", expand=True)
        self._build_queue_tab()
        self._build_saved_tab()
        self.tabs.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # ---- bottom left: preview player with captions
        box = ttk.LabelFrame(left, text="Preview")
        box.pack(fill="x", pady=(8, 0))
        self.player = Player(box)
        self.player.pack(fill="x", padx=8, pady=8)
        self.player.on_time = self._on_player_time
        self.player.on_tracks_changed = self._on_tracks_muted
        self.player.set_caption_style(self.caption_style)

        # ---- right: transcript of whatever is selected
        self.right = ttk.LabelFrame(body, text="Transcript")
        self.right.grid(row=0, column=2, sticky="nsew")
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
        self.version_del_btn = ttk.Button(self.version_bar, text="Delete version", command=self._delete_version)
        self.version_del_btn.pack(side="right")
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
        self._edit_setup()      # double-click a row to fix its wording
        self.text.bind(RIGHT_CLICK, self._on_row_menu)     # right-click a row
        self.text.bind("<Up>", lambda e: self._key_move(-1))
        self.text.bind("<Down>", lambda e: self._key_move(1))
        self.text.bind("<Prior>", lambda e: self._key_move(-10))
        self.text.bind("<Next>", lambda e: self._key_move(10))
        self.text.bind("<Return>", lambda e: self._key_enter())
        self.bind("<space>", self._space_key)      # space plays/pauses (not while typing)
        self._find_setup()      # Ctrl+F
        self._find_theme()
        self._markers_setup()   # M marks a row
        self.text.bind("<Motion>", self._suspect_hover, add="+")
        # A right-aligned tab stop at the right edge, for the track name at the end of each row.
        self.text.bind("<Configure>", lambda e: self.text.configure(
            tabs=(max(e.width - 2 * int(self.text.cget("padx")) - 6, 50), "right")), add="+")

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
        self.tree.bind("<Double-1>", self._open_selected_output)
        self.tree.bind(RIGHT_CLICK, self._queue_menu)

        hint = "Drag files here, or use Add files." if HAS_DND else "Use Add files to choose videos or audio."
        ttk.Label(q, text=hint, style="Muted.TLabel").pack(anchor="w", padx=6, pady=(4, 0))
        qb = ttk.Frame(q)
        qb.pack(fill="x", padx=6, pady=6)
        ttk.Button(qb, text="Add files...", command=self._add_files_dialog).pack(side="left")
        ttk.Button(qb, text="Remove", command=self._remove_selected).pack(side="left", padx=4)
        ttk.Button(qb, text="Clear finished", command=self._clear_finished).pack(side="left")

        self.queue_frame = q
        self.drop_canvas = None
        if HAS_DND:
            for w in (self.tree, q):
                self._make_drop_target(w)

    def _build_saved_tab(self):
        t = ttk.Frame(self.tabs)
        self.tabs.add(t, text="Transcripts")
        # Search box lives in the tab row, right of the "Transcripts" tab (shown only on that tab).
        # The hint is gray text inside the box (not a label on top), so it shares the box's background.
        self.search_entry = ttk.Entry(self.tabs.master, textvariable=self.search_var)
        self._search_placeholder = False
        self.search_var.trace_add("write", lambda *_: self._search_changed())
        self.search_entry.bind("<FocusIn>", lambda e: self._search_focus(True))
        self.search_entry.bind("<FocusOut>", lambda e: self._search_focus(False))
        self.search_entry.bind("<Escape>", lambda e: (self.search_var.set(""), self.saved.focus_set()))

        self.saved = ttk.Treeview(t, columns=("versions", "date"), show="tree headings", selectmode="browse", height=3)
        self.saved.heading("#0", text="Transcript")
        self.saved.heading("versions", text="Versions")
        self.saved.heading("date", text="Latest")
        self.saved.column("#0", width=self.thumb_w + 180)
        self.saved.column("versions", width=78, anchor="center")
        self.saved.column("date", width=132, anchor="center")
        self.saved.pack(fill="both", expand=True, padx=6, pady=(8, 0))
        self.saved.bind("<<TreeviewSelect>>", lambda e: self._on_saved_select())
        self.saved.bind("<Double-1>", lambda e: self._open_saved())
        if HAS_DND:
            self._make_drop_target(self.saved)

        ttk.Label(t, textvariable=self.saved_count_var, style="Muted.TLabel").pack(anchor="w", padx=6, pady=(4, 0))
        sb = ttk.Frame(t)
        sb.pack(fill="x", padx=6, pady=6)
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
            m = re.match(r"\[(\d+):(\d+):(\d+)\]", self.text.get(f"{line}.0", f"{line}.12"))
            if m:
                h, mi, se = (int(g) for g in m.groups())
                self._rows.append((h * 3600 + mi * 60 + se, line))
            self._band_n += 1
            if self._band_n % 2 == 0:
                self.text.tag_add("band", f"{line}.0", f"{line}.0 lineend +1c")

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
        # Leave room for the list above it and the captions and controls below it.
        room = e.height - int(400 * self.ui_scale)
        self.player.max_screen_h = max(min(int(e.height * 0.55), room), 160)

    # ---- the divider between the list/player side and the transcript

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
        self.right.configure(text=title)
        if versions and not self.version_bar.winfo_ismapped():
            self.version_bar.pack(fill="x", padx=8, pady=(8, 0), before=self.text_sb)
        elif not versions and self.version_bar.winfo_ismapped():
            self.version_bar.pack_forget()
        self._hover_line = None
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self._who_by_line = {}
        for entry in entries:
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
        self._band_n = 0
        self._rows = []
        self._now_line = None
        for line in range(1, int(self.text.index("end-1c").split(".")[0])):
            self._band_line(line)
        self.text.see("end" if scroll_end else "1.0")
        self.text.configure(state="disabled")
        self._edit_after_render(editable)
        self._apply_muted_rows()
        self._markers_after_render()
        self._suspects_after_render()
        self._find_after_render()


    def _general_append(self, text, tag=None):
        self.general_log.append((text, tag))
        if self.display is None:
            self._insert(text, tag)

    def _row_at(self, x: int, y: int):
        """The logical line under the mouse if it's a timestamped row, else None."""
        line = int(self.text.index(f"@{x},{y}").split(".")[0])
        if "ts" not in self.text.tag_names(f"{line}.0"):
            return None
        last = self.text.dlineinfo(f"{line}.0 lineend")   # last screen line of a wrapped row
        if last and y > last[1] + last[3]:
            return None                                   # below the final row
        return line

    def _set_hover(self, line):
        if line == self._hover_line:
            return
        self.text.tag_remove("hover", "1.0", "end")
        self._hover_line = line
        if line is not None:
            self.text.tag_add("hover", f"{line}.0", f"{line}.0 lineend +1c")
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
            self.text.tag_add("now", f"{line}.0", f"{line}.0 lineend +1c")

    def _on_player_time(self, t, playing: bool):
        """Called by the player a few times a second: move the blue row to where playback is."""
        rows = self._rows
        muted = getattr(self, "_muted_lines", None)
        if muted:
            rows = [r for r in rows if r[1] not in muted]
        if t is None or not rows:
            self._set_now(None)
            return
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
        if "who" in self.text.tag_names(idx):
            return                          # clicking a name opens "Who is this?" instead
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
        m = re.match(r"\[(\d+):(\d+):(\d+)\]", self.text.get(f"{line}.0", f"{line}.0 lineend"))
        if m:
            h, mi, se = (int(g) for g in m.groups())
            self.player.seek(h * 3600 + mi * 60 + se)
            self._set_now(line)   # don't wait for the next player update

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
        """Space bar: play/pause the preview, unless it's typing (editing a row or a time, a text box)
        or pressing the focused button."""
        if self._editing is not None or getattr(self, "_time_entry", None):
            return None
        w = e.widget
        if isinstance(w, (tk.Entry, ttk.Entry, tk.Spinbox, tk.Listbox, ttk.Button, tk.Button,
                          ttk.Checkbutton, tk.Checkbutton, ttk.Radiobutton, tk.Radiobutton)):
            return None
        if isinstance(w, tk.Text) and w is not self.text:
            return None
        self.player.toggle()
        return "break"

    def _key_enter(self):
        if self._editing is not None:      # Enter while editing a line finishes the edit
            self._end_edit()
            return "break"
        if self._editing is None and self._hover_line is not None and self._hover_line in [l for _, l in self._rows]:
            self._seek_line(self._hover_line)
        return "break"

    def _rows_entries(self, rows, files, track: str = "", track_i: int = 0, indices=None):
        """Transcript rows as display entries: time, then (if known) a photo and name, then the text,
        and (when several tracks are shown together) the track's name at the right end."""
        size = max(int(18 * self.ui_scale), 14)
        entries = []
        for n, (sec, label, name, text, pid) in enumerate(rows):
            meta = {"files": files, "i": indices[n] if indices else n, "orig": text, "track": track, "sec": sec}
            entries.append((f"[{format_timestamp(sec)}]  ", "ts"))
            person = self.people.get(pid)
            if name:
                color = person["color"] if person else "#64748b"
                tag = self._color_tag(color)
                img = self.avatars.get(person, size, name, color)
                who = (files, label)
                entries.append(("\x00img", img, ("who",)))
                entries.append((f" {name}:  ", (tag, "who")))
                entries.append((text, None, None, who, meta))
            else:
                entries.append((text, None, None, None, meta))
            if track and track != name:        # (a person's own mic: the name already says it)
                entries.append((f"\t{track}", f"trk{track_i % 4}"))
            entries.append(("\n", None))
        return entries

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


    def _set_call(self, source, caps):
        """The named speakers, for the player's call view (audio files, or the Audio only view)."""
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
        entries, caps = [], []
        for r, i, name, files, k in rows:
            entries += self._rows_entries([r], files, track=name if many else "", track_i=i, indices=[k])
            caps.append(self._cap(r) + (name if many else "",))   # the track, so muted tracks hide
        return entries, caps


    TRACKS = "\x00tracks"   # the separate-tracks view: every track together, in time order


    SEARCH_HINT = "Search transcripts"

    def _tab_extent(self) -> tuple[int, int]:
        """(right edge of the last tab, tab row height) in pixels, found by probing the notebook."""
        nb = self.tabs
        nb.update_idletasks()

        def tab_at(x, y):
            try:
                i = nb.index(f"@{x},{y}")
                return int(i) if str(i) != "" else -1
            except (tk.TclError, ValueError):
                return -1

        height = 0
        for y in range(1, 80):
            if tab_at(12, y) == 0:
                height = y
        mid = max(height // 2, 6)
        right = 0
        for x in range(0, nb.winfo_width(), 2):
            if tab_at(x, mid) == nb.index("end") - 1:
                right = x
        return right, height or 30

    def _place_search(self, show: bool):
        if not show:
            self.search_entry.place_forget()
            return
        if not getattr(self, "_tab_cache", None) or not self._tab_cache[0]:
            self._tab_cache = self._tab_extent()
        right, height = self._tab_cache
        start = right + 10
        self.search_entry.place(in_=self.tabs, x=start, y=0, relwidth=1.0, width=-start - 2, height=max(height, 26))
        tk.Misc.lift(self.search_entry)

    def _on_tab_changed(self, _event=None):
        self._place_search(self.tabs.index("current") == 1)
        if self.tabs.index("current") == 0:
            self._on_queue_select()
        else:
            self._refresh_saved()
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
            "hf_token": self.hf_token.get().strip(),
            "track_default": self.track_default,
            "track1_is_mix": self.track1_mix.get(),
            "engine": self.engine_pref,
            "captions": self.caption_style,
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
        self.destroy()

    def show_ready(self, problems: list[str]):
        """Called by the loading screen once everything is loaded (or it was skipped)."""
        for msg in problems:
            self._general_append(msg + "\n\n", "err")
        model = self.model_var.get()
        if not problems and is_model_loaded(model):
            self.status_var.set(f"Ready. '{model}' model loaded. "
                                + (f"{self._count(PENDING)} file(s) ready." if self.jobs else "Add files to get started."))
        self.deiconify()
        if self._start_zoomed:
            self.state("zoomed")
        self.after(150, lambda: setattr(self, "_tab_cache", self._tab_extent()))   # measure once it's visible
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
                    import traceback
                    traceback.print_exc()
                    self.status_var.set(f"Something went wrong updating the window: {exc}")
        finally:
            self.after(100, self._poll_events)

    def _handle_event(self, ev):
        kind = ev[0]
        if kind == "begin":
            _, job, n, total = ev
            self._set_status(job, WORKING)
            self.tree.see(job["iid"])
            self.status_var.set(f"Transcribing {n} of {total}: {Path(job['path']).name}")
            if self.follow_live and self.tabs.index("current") == 0:
                self.tree.selection_set(job["iid"])
                self._show_job(job)
        elif kind == "segment":
            _, job, seg, pct = ev
            job["segs"].append((seg.start, seg.text))
            self._job_append(job, f"[{format_timestamp(seg.start)}]  ", "ts")
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
    app = App()
    app.withdraw()                      # main window stays hidden until everything is loaded
    if len(sys.argv) > 1:  # files dropped onto the launcher or passed on the command line
        app._add_paths(sys.argv[1:])
    LoadingScreen(app)
    app.mainloop()


if __name__ == "__main__":
    main()
