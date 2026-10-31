"""Embedded video/audio player with captions, built on VLC (python-vlc).

Needs VLC media player installed (videolan.org, 64-bit to match 64-bit Python) and
`pip install python-vlc`. Without them, the panel shows how to enable it instead.
"""

from __future__ import annotations

import bisect
import os
import sys
import tkinter as tk
from tkinter import font as tkfont
from pathlib import Path
from tkinter import ttk
from typing import Callable, Optional

from transcriber.fonts import CAPTION_FONT, PIP, RIGHT_CLICK, ui_font
from transcriber.icons import Tooltip, glyph, icon_family

try:
    import vlc
    _VLC_ERROR = None
except Exception as exc:  # ImportError, or OSError when the VLC app (libvlc) isn't installed
    vlc = None
    _VLC_ERROR = exc

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}

# Caption source: returns [(start_seconds, text), ...] sorted by start. Called often, so keep it cheap.
CaptionSource = Callable[[], list]


DEFAULT_CAPTIONS = {"mode": "below", "size": 100, "background": "box", "color": "white",
                    "position": "bottom", "names": True, "split": True, "cuts": True, "sounds": False,
                    "overlap": "layer",
                    "speaker": "name"}
CAPTION_SIZES = {"small": (0.045, 10), "medium": (0.055, 11), "large": (0.07, 13), "xlarge": (0.09, 16)}
SIZE_PERCENTS = (50, 75, 100, 150, 200, 300, 400)              # caption size choices, like YouTube's
_OLD_SIZES = {"small": 75, "medium": 100, "large": 150, "xlarge": 200}


def size_percent(style: dict) -> int:
    """The caption size as a percent of normal (older settings said small / medium / large)."""
    v = (style or {}).get("size", 100)
    if isinstance(v, str):
        v = _OLD_SIZES.get(v, v.rstrip("%"))
    try:
        return min(max(int(v), 25), 400)
    except (TypeError, ValueError):
        return 100


def caption_scale(style: dict) -> tuple:
    """(height of on-video text as a share of the picture, font size of the strip below) for a style."""
    pct = size_percent(style) / 100
    return 0.055 * pct, max(round(11 * pct), 6)
CAPTION_COLORS = {"white": "#ffffff", "yellow": "#ffe14d", "gray": "#d6d6d6"}


def _lighten(color: str, t: float = 0.3) -> str:
    """A person's color, a bit lighter so it reads well on the black caption strip."""
    try:
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, TypeError):
        return "#c8d0d8"
    return "#" + "".join(f"{round(x + (255 - x) * t):02x}" for x in (r, g, b))


def fmt_time(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"



_libtk = None


def _mac_nsview(wid: int):
    """On macOS VLC draws into an NSView, but Tk's window id isn't one: ask Tk's own library for the
    view behind it (the same trick as python-vlc's tkvlc example). None if that isn't possible;
    VLC then shows the video in its own window."""
    global _libtk
    try:
        import ctypes
        import ctypes.util
        import tkinter
        if _libtk is None:
            from pathlib import Path as _P
            cands = [str(p) for d in {_P(tkinter.__file__).parent.parent, _P(sys.base_prefix) / "lib"}
                     for p in d.glob("libtk8*.dylib")]
            name = cands[0] if cands else ctypes.util.find_library("tk8.6") or ctypes.util.find_library("tk")
            _libtk = ctypes.cdll.LoadLibrary(name)
        for fn in ("TkMacOSXDrawableView", "TkMacOSXGetRootControl"):
            f = getattr(_libtk, fn, None)
            if f is not None:
                f.restype = ctypes.c_void_p
                f.argtypes = [ctypes.c_void_p]
                view = f(wid)
                if view:
                    return view
    except Exception:
        pass
    return None

class Player(ttk.Frame):
    """Video area + caption strip + controls. Call load(path, captions) and seek(seconds)."""

    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self.path: Optional[str] = None
        self.captions: CaptionSource = lambda: []
        self.on_time: Optional[Callable[[Optional[float], bool], None]] = None   # (seconds or None, playing)
        self.length = 0.0
        self._dragging = False
        self._was_playing = False
        self._stop_at = None                   # play_range: pause when playback reaches this time
        # Audio tracks: [{"n", "name", "transcribed"}]. VLC plays one track per player, so the first
        # track that's on plays with the video and each other one gets its own hidden audio player.
        self.tracks: list[dict] = []
        self.track_on: dict[int, bool] = {}
        self.on_tracks_changed: Optional[Callable[[set], None]] = None   # muted track names
        self._extras: dict[int, object] = {}
        self.volumes: dict[int, Optional[int]] = {}   # per-track volume (0-100) while focusing on someone
        self.focus: Optional[dict] = None              # {"name", "track": n or None, "spans": [(start, end)]}
        self.on_focus_changed: Optional[Callable[[Optional[str]], None]] = None
        self._track_applied = True
        self._priming = False
        self.on_caption_edit: Optional[Callable[[tuple], None]] = None   # double-click on a caption

        self.screen = tk.Frame(self, background="black", height=200)
        self.screen.pack(fill="x")
        self.screen.pack_propagate(False)
        self.bind("<Configure>", self._keep_16_by_9)
        # VLC draws into this inner panel. Hiding it hides VLC's own video window too, which on
        # Windows would otherwise sit on top of the call tiles (Audio only) whatever Tk stacks above.
        self.surface = tk.Frame(self.screen, background="black")
        self.surface.place(relx=0, rely=0, relwidth=1, relheight=1)

        # Shown on top of the black screen when there's no video picture (no file, audio file, errors).
        self.overlay = tk.Label(self.screen, background="black", foreground="#bdbdbd",
                                font=ui_font(10), justify="center", wraplength=380)
        self._cover(True)

        # Call view: for audio-only files, the people talking as tiles, like a video call.
        self.call = tk.Canvas(self.screen, background="black", highlightthickness=0, borderwidth=0)
        self.call.bind("<Configure>", lambda e: self._draw_call(force=True))
        self.call.bind("<Button-1>", lambda e: self.toggle())
        self._tiles: list = []
        self._call_active = None
        self._call_imgs: dict = {}

        self.caption = self._make_caption(self, 11)
        self.caption.pack(fill="x")
        self._caption_shown = None
        self._fs = None            # (window, video frame, caption) while fullscreen

        controls = ttk.Frame(self)
        controls.pack(fill="x", pady=(6, 0))
        self._controls = controls
        self.cap_style = dict(DEFAULT_CAPTIONS)
        self._marq_ready = False
        self.play_btn = ttk.Button(controls, text=glyph(self, "play"), style="Icon.TButton", width=3, command=self.toggle)
        self.play_btn.pack(side="left")
        self.play_tip = Tooltip(self.play_btn, "Play")
        self.focus_lbl = ttk.Label(controls, text="", style="Muted.TLabel", cursor="hand2")
        self.focus_lbl.bind("<Button-1>", lambda e: self.set_focus(None))
        self._focus_tip = Tooltip(self.focus_lbl, "Click to stop focusing")
        self.mute_btn = ttk.Button(controls, text=glyph(self, "volume"), style="Icon.TButton", width=3, command=self.toggle_mute)
        self.mute_btn.pack(side="right")
        self.mute_tip = Tooltip(self.mute_btn, "Mute (right-click for tracks)")
        self.mute_btn.bind(RIGHT_CLICK, self._track_menu)
        self.rate = 1.0
        self.rate_btn = ttk.Button(controls, text="1\u00d7", width=5, command=self._rate_menu)
        self.rate_btn.pack(side="right", padx=(4, 0))
        Tooltip(self.rate_btn, "Playback speed")
        self.view_btn = ttk.Button(controls, text=glyph(self, "audioview"), style="Icon.TButton", width=3,
                                   command=self.toggle_audio_only)
        self.view_btn.pack(side="right", padx=(4, 0))
        self.view_tip = Tooltip(self.view_btn, "Audio only")
        self.time_var = tk.StringVar(value="0:00 / 0:00")
        ttk.Label(controls, textvariable=self.time_var, width=13, anchor="e").pack(side="right", padx=(6, 4))
        self.scale = ttk.Scale(controls, from_=0, to=1, orient="horizontal")
        self.scale.pack(side="left", fill="x", expand=True, padx=8)
        # Click anywhere on the bar to jump there; drag to scrub. (These replace Tk's default,
        # which steps toward the ends when you click the bar.)
        self.scale.bind("<ButtonPress-1>", self._drag_start)
        self.scale.bind("<B1-Motion>", self._drag_move)
        self.scale.bind("<ButtonRelease-1>", self._drag_end)

        self.available = vlc is not None
        self.mp = None
        if self.available:
            try:
                self.instance = self._new_instance()
                self.mp = self.instance.media_player_new()
                self.mp.video_set_mouse_input(False)   # let clicks reach Tk (click video to play/pause)
                self.mp.video_set_key_input(False)
                self.after_idle(self._attach)
            except Exception as exc:
                self.available = False
                self._vlc_error = exc
        if not self.available:
            self._set_controls("disabled")
            self.overlay.configure(
                text=("Video preview needs VLC.\n\nInstall VLC media player from videolan.org,\nthen restart the app."
                      if getattr(sys, "frozen", False) else
                      "Video preview needs VLC.\n\n1. Install VLC media player (64-bit) from videolan.org\n"
                      f"2. Run:  {PIP} install python-vlc\n3. Restart the app"))
        else:
            self._set_controls("disabled")
            self.overlay.configure(text="Select a file to preview it here")
        for w in (self.screen, self.surface, self.overlay, self.caption):
            w.bind("<Button-1>", lambda e: self.toggle())
        for w in (self.screen, self.surface, self.overlay, self.call, self.caption):
            w.bind(RIGHT_CLICK, self._video_menu)
        for w in (self.screen, self.surface):
            w.bind("<Double-Button-1>", self._video_double)
        self.caption.bind("<Double-Button-1>", lambda e: self._edit_caption())
        self._make_fs_button()
        self._tick()
        self._hover_check()

    # ---------------------------------------------------------------- setup

    def _attach(self, surface=None):
        """Hand a black frame to VLC as its drawing surface (the preview area, or fullscreen)."""
        wid = (surface or self.surface).winfo_id()
        if sys.platform == "win32":
            self.mp.set_hwnd(wid)
        elif sys.platform == "darwin":
            view = _mac_nsview(wid)
            if view:
                self.mp.set_nsobject(view)
        else:
            self.mp.set_xwindow(wid)

    max_screen_h = 10_000       # the app lowers this so a wide window doesn't make the video too tall

    fill_height = False         # True: the picture area takes all the height it's given (letterboxed)

    def fit_screen(self):
        self._keep_16_by_9(type("E", (), {"width": self.winfo_width()})())

    def _keep_16_by_9(self, event):
        h = max(self.max_screen_h if self.fill_height else min(int(event.width * 9 / 16), self.max_screen_h), 120)
        if abs(int(self.screen.cget("height")) - h) > 2:
            self.screen.configure(height=h)
        self.overlay.configure(wraplength=max(event.width - 40, 100))

    def _cover(self, show: bool):
        """Cover the whole video area (hides any leftover frame) or reveal VLC's picture."""
        if show:
            self.overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        else:
            self.overlay.place_forget()

    RATES = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)

    def _rate_menu(self):
        dark = str(ttk.Style(self).lookup("TFrame", "background")).lower() in ("#1c1c1c", "#202020", "#2b2b2b")
        menu = tk.Menu(self, tearoff=0, background="#2b2b2b" if dark else "#ffffff",
                       foreground="#e8e8e8" if dark else "#1c1c1c", activebackground="#383838" if dark else "#ebedf0",
                       activeforeground="#e8e8e8" if dark else "#1c1c1c", font=ui_font(10),
                       relief="flat", borderwidth=1)
        for r in self.RATES:
            label = f"{r:g}\u00d7" + ("   \u2713" if abs(r - self.rate) < 0.01 else "")
            menu.add_command(label=label, command=lambda r=r: self.set_rate(r))
        b = self.rate_btn
        try:
            menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() - len(self.RATES) * 26)
        finally:
            menu.grab_release()

    def set_rate(self, r: float):
        self.rate = r
        self.rate_btn.configure(text=f"{r:g}\u00d7")
        if self.available:
            self.mp.set_rate(r)
            for extra in self._extras.values():
                extra.set_rate(r)

    def _set_controls(self, state):
        for w in (self.play_btn, self.mute_btn, self.rate_btn):
            w.configure(state=state)
        audio_file = getattr(self, "_is_audio_file", False) or getattr(self, "_audio_cover", False)
        self.view_btn.configure(state="disabled" if state == "disabled" or audio_file else "normal")
        self.scale.state(["disabled"] if state == "disabled" else ["!disabled"])

    # ---------------------------------------------------------------- public

    def load(self, path: Optional[str], captions: Optional[CaptionSource] = None, note: str = ""):
        """Show this file (or clear with None). Keeps playing position if it's the same file."""
        self.captions = captions or (lambda: [])
        self.display_captions = None     # caption-sized pieces to show, when set (see saved_ui)
        if not self.available:
            return
        if path == self.path:
            return
        if self._fs:
            self.toggle_fullscreen()
        self._drop_extras()
        self.tracks, self.track_on = [], {}
        self.set_call([])
        if self.focus:
            self.set_focus(None)
        self.path = path
        self.length = 0.0
        self._marq_ready = False
        self._show_caption(None)
        self.mp.stop()
        if not path or not os.path.exists(path):
            self.overlay.configure(text=note or ("The original file isn't available" if path else
                                                 "Select a file to preview it here"))
            self.overlay.configure(font=ui_font(10))
            self._cover(True)
            self._set_controls("disabled")
            self.time_var.set("0:00 / 0:00")
            self._audio_cover = False
            self._layout_caption()
            return
        is_audio = Path(path).suffix.lower() in AUDIO_EXTS
        self._audio_cover = is_audio
        if is_audio:
            self.overlay.configure(text="♪\n" + Path(path).name, font=ui_font(13))
            self._cover(True)
        else:
            self._cover(False)
        media = self.instance.media_new(path)
        self.mp.set_media(media)
        self._is_audio_file = is_audio
        self._refresh_view()
        self._track_applied = False   # the new file's tracks show up once it starts
        self._set_controls("normal")
        self._prime()

    def _end_priming(self):
        """The user pressed play or jumped somewhere before the first-frame preview finished."""
        if self._priming:
            self._priming = False
            self.mp.audio_set_mute(False)

    # ---------------------------------------------------------------- call view

    audio_only_view = False      # right-click the video > Audio only (stays on for other files)

    def set_call(self, tiles: list, shape: str = "circle", audio_file: Optional[bool] = None):
        """The people in the transcript, shown like a call for audio files (or any file in the
        Audio only view): tiles = [{"name", "color", "person"}]. Whoever is talking lights up."""
        self._all_tiles, self._call_shape = tiles, shape
        if audio_file is not None:
            self._is_audio_file = audio_file
        self._refresh_view()

    def _refresh_view(self):
        """Picture, call tiles, or the music note, depending on the file and the Audio only view."""
        tiles = getattr(self, "_all_tiles", [])
        audio = getattr(self, "_is_audio_file", False) or self.audio_only_view
        self._tiles = tiles if audio else []
        self._call_active = "redraw"
        self._caption_shown = "redraw"         # captions may move between the picture and the strip below
        # A video in the Audio only view with no one to show: the file's name on the same dark panel
        # (a panel the video can't paint over, unlike a label on top of it).
        self._note_view = bool(self.path and self.audio_only_view and not self._tiles
                               and not getattr(self, "_audio_cover", False)
                               and not getattr(self, "_is_audio_file", False))
        if (self._tiles or self._note_view) and self.path:
            self.overlay.place_forget()
            self.surface.place_forget()            # takes VLC's video window out of sight
            self.call.place(relx=0, rely=0, relwidth=1, relheight=1)
            tk.Misc.lift(self.call)
            self._draw_call(force=True)
        else:
            self.call.place_forget()
            if not self.surface.winfo_manager():
                self.surface.place(relx=0, rely=0, relwidth=1, relheight=1)
                tk.Misc.lower(self.surface)
            if self.path and self.audio_only_view and not getattr(self, "_audio_cover", False):
                self.overlay.configure(text="\u266a\n" + Path(self.path).name, font=ui_font(13))
                self._cover(True)
            elif getattr(self, "_audio_cover", False):
                self._cover(True)
            elif self.path:
                self._cover(False)
        self._layout_caption()
        self.refresh_captions()
        if self.available and self.path:
            self._show_caption(self._captions_at(max(self.mp.get_time() / 1000, 0)))
        on = self.audio_only_view
        audio_file = getattr(self, "_is_audio_file", False) or getattr(self, "_audio_cover", False)
        self.view_btn.configure(text=glyph(self, "videoview" if on else "audioview"),
                                state="disabled" if audio_file or not self.path else "normal")
        self.view_tip.set_text("Show video" if on else "Audio only")

    def toggle_audio_only(self):
        self.audio_only_view = not self.audio_only_view
        self._refresh_view()

    def _video_menu(self, e):
        if not self.available or not self.path:
            return "break"
        dark = str(ttk.Style(self).lookup("TFrame", "background")).lower() in ("#1c1c1c", "#202020", "#2b2b2b")
        menu = tk.Menu(self, tearoff=0, background="#2b2b2b" if dark else "#ffffff",
                       foreground="#e8e8e8" if dark else "#1c1c1c", activebackground="#383838" if dark else "#ebedf0",
                       activeforeground="#e8e8e8" if dark else "#1c1c1c", selectcolor="#e8e8e8" if dark else "#1c1c1c",
                       font=ui_font(10), relief="flat", borderwidth=1)
        self._audio_only_var = tk.BooleanVar(value=self.audio_only_view)
        menu.add_checkbutton(label="Audio only", variable=self._audio_only_var, command=self.toggle_audio_only)
        if not getattr(self, "_audio_cover", False) and not self._tiles:
            menu.add_command(label="Fullscreen", command=self.toggle_fullscreen)
        # Caption size, right here while watching (also in Settings > Captions)
        sizes = tk.Menu(menu, tearoff=0, background=menu.cget("background"), foreground=menu.cget("foreground"),
                        activebackground=menu.cget("activebackground"), activeforeground=menu.cget("activeforeground"),
                        selectcolor=menu.cget("selectcolor"), font=ui_font(10), relief="flat", borderwidth=1)
        self._size_var = tk.IntVar(value=size_percent(self.cap_style))
        for pct in SIZE_PERCENTS:
            sizes.add_radiobutton(label=f"{pct}%", variable=self._size_var, value=pct,
                                  command=lambda p=pct: self._pick_caption_size(p))
        menu.add_separator()
        menu.add_cascade(label="Caption size", menu=sizes)
        if self.on_save_with_captions and not getattr(self, "_audio_cover", False):
            menu.add_command(label="Save video with captions...", command=self.on_save_with_captions)
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()
        return "break"

    on_caption_size: Optional[Callable[[int], None]] = None     # the app saves the choice
    on_save_with_captions: Optional[Callable[[], None]] = None   # a copy with a subtitle track

    def _pick_caption_size(self, pct: int):
        if self.on_caption_size:
            self.on_caption_size(pct)
        else:
            self.set_caption_style({**self.cap_style, "size": pct})

    def _call_img(self, tile, size: int, gray: bool):
        key = (tile["name"], size, gray, self._call_shape)
        if key not in self._call_imgs:
            from PIL import ImageEnhance, ImageTk
            from transcriber.people import avatar_pil
            img = avatar_pil(tile.get("person"), size, tile["name"], tile["color"], shape=self._call_shape)
            if gray:
                alpha = img.getchannel("A")
                img = ImageEnhance.Brightness(img.convert("L").convert("RGBA")).enhance(0.45)
                img.putalpha(alpha)
            self._call_imgs[key] = ImageTk.PhotoImage(img)
            if len(self._call_imgs) > 200:
                self._call_imgs.clear()
                return self._call_img(tile, size, gray)
        return self._call_imgs[key]

    def _draw_call(self, force: bool = False):
        if not (self._tiles or getattr(self, "_note_view", False)) or not self.call.winfo_ismapped():
            return
        cap = self._caption_at(max(self.mp.get_time() / 1000, 0)) if (self.available and self.path) else None
        # Who is talking: the speaker's name, or (no speakers, separate tracks) the line's track.
        active = (cap[2] if len(cap) > 3 and cap[2] else cap[4] if len(cap) > 4 and cap[4] else None) if cap else None
        if active == self._call_active and not force:
            return
        self._call_active = active
        c = self.call
        c.delete("all")
        w, h = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        if not self._tiles:
            c.create_text(w / 2, h / 2 - 14, text="\u266a", fill="#9a9a9a", font=ui_font(22))
            c.create_text(w / 2, h / 2 + 18, text=Path(self.path).name if self.path else "", fill="#d0d0d0",
                          font=ui_font(11), width=max(w - 40, 50), justify="center")
            return
        n = len(self._tiles)
        cols = 1 if n == 1 else 2 if n <= 4 else 3
        rows = (n + cols - 1) // cols
        gap = 6
        tw, th = (w - gap * (cols + 1)) / cols, (h - gap * (rows + 1)) / rows
        size = int(max(min(tw, th) * 0.5, 16))
        for k, tile in enumerate(self._tiles):
            r, col = divmod(k, cols)
            x0 = gap + col * (tw + gap)
            if r == rows - 1 and n % cols:            # center an unfinished last row
                x0 += (cols - n % cols) * (tw + gap) / 2
            y0 = gap + r * (th + gap)
            talking = tile.get("track", tile["name"]) == active
            dim = active is not None and not talking
            outline = _lighten(tile["color"], 0.15) if talking else "#262626"
            self._round_rect(x0, y0, x0 + tw, y0 + th, 10, fill="#151515" if not talking else "#1c1c1c",
                             outline=outline, width=3 if talking else 1)
            cx, cy = x0 + tw / 2, y0 + th / 2 - min(14, th * 0.08)
            c.create_image(cx, cy, image=self._call_img(tile, size, dim))
            c.create_text(cx, cy + size / 2 + 14, text=tile["name"], fill="#6b6b6b" if dim else "#f2f2f2",
                          font=ui_font(10 if size > 40 else 9, bold=True))

    def _round_rect(self, x0, y0, x1, y1, r, **kw):
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
               x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        return self.call.create_polygon(pts, smooth=True, **kw)

    def set_audio_track(self, n: Optional[int]):
        """Hear just one track (0-based), or the file's default with None."""
        self.set_tracks([{"n": n, "name": "", "transcribed": True}] if n is not None else [])

    def set_tracks(self, tracks: list[dict]):
        """The file's audio tracks: [{"n": 0-based, "name": shown name, "transcribed": bool}].
        Tracks that weren't transcribed start muted (unless none of them were)."""
        old = {t["n"]: self.track_on.get(t["n"]) for t in self.tracks} if tracks == self.tracks else {}
        self.tracks = tracks
        any_done = any(t["transcribed"] for t in tracks)
        self.track_on = {t["n"]: old.get(t["n"]) if old.get(t["n"]) is not None else (t["transcribed"] or not any_done)
                         for t in tracks}
        self._normalize()
        self._tracks_changed()

    def _everything(self):
        """The track that already holds all the others (an OBS-style main track), if marked."""
        return next((t["n"] for t in self.tracks if t.get("everything")), None)

    def _isolated(self) -> list[int]:
        e = self._everything()
        return [t["n"] for t in self.tracks if t["n"] != e]

    def _normalize(self):
        """The 'everything' track counts as on exactly when every other track is on."""
        e = self._everything()
        if e is not None:
            iso = self._isolated()
            if not any(t["transcribed"] for t in self.tracks if t["n"] != e) and self.track_on.get(e):
                for n in iso:                      # mixed transcript: everything on
                    self.track_on[n] = True
            self.track_on[e] = bool(iso) and all(self.track_on.get(n) for n in iso)

    def muted_names(self) -> set:
        e = self._everything()
        return {t["name"] for t in self.tracks if t["n"] != e and not self.track_on.get(t["n"], True) and t["name"]}

    def _tracks_changed(self):
        self._track_applied = False
        self._apply_track()
        self._sync_extras()
        self._caption_shown = "redraw"
        self.refresh_captions()
        if self.on_tracks_changed:
            self.on_tracks_changed(self.muted_names())

    def _track_menu(self, e):
        if not self.available or not self.path or len(self.tracks) < 2:
            return "break"
        dark = str(ttk.Style(self).lookup("TFrame", "background")).lower() in ("#1c1c1c", "#202020", "#2b2b2b")
        menu = tk.Menu(self, tearoff=0, background="#2b2b2b" if dark else "#ffffff",
                       foreground="#e8e8e8" if dark else "#1c1c1c", activebackground="#383838" if dark else "#ebedf0",
                       activeforeground="#e8e8e8" if dark else "#1c1c1c", selectcolor="#e8e8e8" if dark else "#1c1c1c",
                       font=ui_font(10), relief="flat", borderwidth=1)
        menu.add_command(label="Play these tracks", state="disabled")
        self._menu_vars = []
        for t in self.tracks:
            var = tk.BooleanVar(value=self.track_on.get(t["n"], True))
            self._menu_vars.append(var)
            label = t["name"] or f"Track {t['n'] + 1}"
            if t.get("everything"):
                label += "   (everything)"
            elif not t["transcribed"]:
                label += "   (not transcribed)"
            menu.add_checkbutton(label=label, variable=var,
                                 command=lambda n=t["n"], v=var: self._set_track_on(n, v.get()))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _set_track_on(self, n: int, on: bool):
        if n == self._everything():
            for m in self._isolated():           # Track 1 on/off = all the others on/off
                self.track_on[m] = on
        self.track_on[n] = on
        self._normalize()
        self._tracks_changed()

    def _enabled(self) -> list[int]:
        """The tracks to actually play. With an 'everything' track: that one alone when every track
        is on, otherwise just the separate tracks that are on (so nothing plays twice)."""
        e = self._everything()
        if e is not None and self.focus and self.focus.get("track") is not None:
            return [n for n in self._isolated() if self.track_on.get(n, True)]   # focusing needs them apart
        if e is not None:
            iso = [n for n in self._isolated() if self.track_on.get(n, True)]
            return [e] if self.track_on.get(e) else iso
        return [t["n"] for t in self.tracks if self.track_on.get(t["n"], True)]

    def _apply_track(self):
        """The video's own player plays the first track that's on (or no sound if all are off)."""
        if self._track_applied or not self.available or not self.path:
            return
        desc = self.mp.audio_get_track_description() or []
        ids = [d[0] for d in desc if d[0] != -1]          # -1 is VLC's "Disable" entry
        if not ids:
            return                                          # not ready yet; _tick tries again
        if self._file_mode():
            # Separate recordings: the shown file plays its own sound only if its row is on.
            own = [n for n in self._enabled() if self._track(n).get("file") == self.path]
            want = ids[0] if own else -1
        elif self.tracks:
            on = self._enabled()
            want = ids[on[0]] if on and on[0] < len(ids) else -1
        else:
            want = ids[0]
        if self.mp.audio_get_track() != want:
            self.mp.audio_set_track(want)
        self._track_applied = True

    # ---- extra audio players for the other tracks that are on

    def _file_mode(self) -> bool:
        """Tracks are separate files (a combined recording), each with its own start time."""
        return any(t.get("file") for t in self.tracks)

    def _track(self, n) -> dict:
        return next((t for t in self.tracks if t["n"] == n), {})

    def _sync_extras(self):
        if not self.available or not self.path:
            return
        if self._file_mode():
            wanted = {n for n in self._enabled() if self._track(n).get("file") != self.path}
        else:
            wanted = set(self._enabled()[1:]) if self.tracks else set()
        for n in list(self._extras):
            if n not in wanted:
                try:
                    self._extras[n].stop()
                    self._extras[n].release()
                except Exception:
                    pass
                del self._extras[n]
        for n in wanted - set(self._extras):
            try:
                tr = self._track(n)
                if tr.get("file"):
                    media = self.instance.media_new(tr["file"], ":no-video")
                else:
                    media = self.instance.media_new(self.path, ":no-video", f":audio-track={n}")
                extra = self.instance.media_player_new()
                extra.set_media(media)
                extra.audio_set_mute(self.mp.audio_get_mute())
                self._extras[n] = extra
            except Exception:
                pass
        self._follow_extras(force=True)

    def _follow_extras(self, force: bool = False):
        """Keep the extra players playing, paused and positioned with the video."""
        if not self._extras:
            return
        playing = self.mp.is_playing() and not self._priming
        main_t = self.mp.get_time()
        muted = self.mp.audio_get_mute()
        for n, extra in self._extras.items():
            # A separate recording that started later: its own time is the shared time minus its start.
            off = int(self._track(n).get("offset", 0) * 1000)
            t = main_t - off
            try:
                if extra.audio_get_mute() != muted:
                    extra.audio_set_mute(muted)
                if playing and abs(extra.get_rate() - self.rate) > 0.01:
                    extra.set_rate(self.rate)
                if self.volumes.get(n) is not None and extra.audio_get_volume() != self.volumes[n]:
                    extra.audio_set_volume(self.volumes[n])
                if playing and t >= 0:
                    if not extra.is_playing():
                        extra.play()
                        self.after(120, lambda x=extra, off=off: x.set_time(max(self.mp.get_time() - off, 0)))
                    elif force or abs(extra.get_time() - t) > 250:
                        extra.set_time(max(t, 0))
                elif extra.is_playing():
                    extra.set_pause(1)
                    extra.set_time(max(t, 0))
                elif force and t >= 0:
                    extra.set_time(t)
            except Exception:
                pass

    # ---- focus: one person loud, everyone else quiet

    FOCUS_LOW = 15      # volume (out of 100) for everyone else

    def set_focus(self, name: Optional[str], track: Optional[int] = None, spans=None):
        """Focus on a person: their own track at full volume and the others low, or (when everyone
        is on one track) full volume only while they're talking."""
        self.focus = {"name": name, "track": track, "spans": sorted(spans or [])} if name else None
        if self.focus:
            self.focus_lbl.configure(text=f"\u25ce {name}  \u2715")
            if not self.focus_lbl.winfo_ismapped():
                self.focus_lbl.pack(side="left", padx=(8, 0), after=self.play_btn)
        else:
            self.focus_lbl.pack_forget()
            self.volumes = {}
            if self.available:
                self.mp.audio_set_volume(100)
                for x in self._extras.values():
                    x.audio_set_volume(100)
        self._track_applied = False
        self._apply_track()
        self._sync_extras()
        if self.on_focus_changed:
            self.on_focus_changed(name)

    def _focus_volume(self, n, t: float) -> int:
        f = self.focus
        if not f:
            return 100
        if f["track"] is not None:
            return 100 if n == f["track"] else self.FOCUS_LOW
        import bisect as _b
        i = _b.bisect_right([a for a, _ in f["spans"]], t) - 1
        talking = i >= 0 and t < f["spans"][i][1]
        return 100 if talking else self.FOCUS_LOW

    def _apply_volumes(self, t: float):
        if not self.focus or not self.available:
            return
        if self._file_mode():
            main_n = next((n for n in self._enabled() if self._track(n).get("file") == self.path), None)
        else:
            on = self._enabled()
            main_n = on[0] if on else None
        v = self._focus_volume(main_n, t)
        if self.mp.audio_get_volume() != v:
            self.mp.audio_set_volume(v)
        self.volumes = {n: self._focus_volume(n, t) for n in self._extras}

    def _drop_extras(self):
        for extra in self._extras.values():
            try:
                extra.stop()
                extra.release()
            except Exception:
                pass
        self._extras = {}

    def seek(self, seconds: float, play: bool = True, quick: bool = False):
        """Jump there. quick: scrubbing (many jumps a second): the frame and captions update, the
        extra players catch up once the scrubbing settles."""
        if not self.available or not self.path:
            return
        self._end_priming()
        if not quick:
            self._stop_at = None
        state = self.mp.get_state()
        if state in (vlc.State.Ended, vlc.State.Stopped, vlc.State.NothingSpecial, vlc.State.Error):
            self.mp.play()

            def jump():
                self.mp.set_time(int(seconds * 1000))
                if not play:
                    self.mp.set_pause(1)   # jumped while stopped: show that spot without playing
                self.after(100, lambda: self._follow_extras(force=True))
            self.after(150, jump)
        else:
            self.mp.set_time(int(seconds * 1000))
            if play and not self.mp.is_playing():
                self.mp.play()
            self._follow_soon(force=True)
        if quick:                              # the captions for that spot now, not at the next tick
            try:
                self._show_caption(self._captions_at(max(seconds, 0.0)))
                self.time_var.set(f"{fmt_time(seconds)} / {fmt_time(self.length)}")
                if self.length and not self._dragging:
                    self.scale.set(seconds / self.length)
            except Exception:
                pass

    def _follow_soon(self, force: bool = False):
        """The extra players catch up with the video a moment later (one catch-up for a burst of jumps)."""
        if not self._extras:
            return
        if getattr(self, "_follow_job", None) is not None:
            self.after_cancel(self._follow_job)
        self._follow_job = self.after(120, lambda: (setattr(self, "_follow_job", None), self._follow_extras(force=force)))

    def play_range(self, start: float, end: float):
        """Play from start and pause at end (a stretch marked on the timeline), captions and all."""
        if not self.available or not self.path or end <= start:
            return
        self.seek(start, play=True)
        self._stop_at = float(end)
        if getattr(self, "_range_job", None) is None:
            self._range_job = self.after(50, self._range_watch)

    def _range_watch(self):
        self._range_job = None
        end = getattr(self, "_stop_at", None)
        if end is None or not self.available or not self.path:
            return
        try:
            t = self.mp.get_time() / 1000
            if self.mp.is_playing() and t >= end - 0.03:
                self.mp.set_pause(1)
                self.mp.set_time(int(end * 1000))
                self._stop_at = None
                self._follow_soon(force=True)
                return
        except Exception:
            self._stop_at = None
            return
        self._range_job = self.after(50, self._range_watch)

    def toggle(self):
        if not self.available or not self.path:
            return
        self._stop_at = None                   # (a marked stretch was playing: carry on past it)
        if self._priming:  # still showing the first frame: treat as "play"
            self._end_priming()
            self.mp.set_pause(0)
            return
        if self.mp.is_playing():
            self.mp.pause()
        else:
            if self.mp.get_state() == vlc.State.Ended:
                self.mp.stop()
            self.mp.play()
        self.after(120, lambda: self._follow_extras(force=True))

    def toggle_mute(self):
        if self.available:
            self.mp.audio_toggle_mute()
            self.after(50, self._follow_extras)

    def stop(self):
        if self.available:
            self.mp.stop()

    def release(self):
        if self.available:
            self._drop_extras()
            try:
                self.mp.stop()
                self.mp.release()
                self.instance.release()
            except Exception:
                pass

    # ---------------------------------------------------------------- internals

    def _prime(self):
        """Play muted for a moment, then pause at the start, so the first frame shows instead of black."""
        self._priming = True
        self.mp.audio_set_mute(True)
        self.mp.play()

        def settle(tries=0):
            if not self._priming:
                return
            if self.mp.get_state() == vlc.State.Playing or tries > 30:
                self.mp.set_pause(1)
                self.mp.set_time(0)
                self.mp.audio_set_mute(False)
                self._priming = False
            else:
                self.after(50, lambda: settle(tries + 1))
        self.after(50, settle)

    def _fraction_at(self, x: int) -> float:
        """Where along the bar this x position is, from 0.0 (start) to 1.0 (end)."""
        pad = 8   # roughly half the slider knob, so clicking an end lands on the end
        width = max(self.scale.winfo_width() - 2 * pad, 1)
        return min(max((x - pad) / width, 0.0), 1.0)

    def _drag_start(self, e):
        if not (self.available and self.path and self.length):
            return "break"
        self._dragging = True
        self._was_playing = self.mp.is_playing()
        self._drag_move(e)
        return "break"

    def _drag_move(self, e):
        if not self._dragging:
            return "break"
        frac = self._fraction_at(e.x)
        self.scale.set(frac)
        t = frac * self.length
        self.time_var.set(f"{fmt_time(t)} / {fmt_time(self.length)}")
        self._show_caption(self._captions_at(t))
        return "break"

    def _drag_end(self, e):
        if not self._dragging:
            return "break"
        frac = self._fraction_at(e.x)
        self.scale.set(frac)
        self._dragging = False
        self.seek(frac * self.length, play=self._was_playing)
        return "break"

    def _captions_at(self, t: float) -> list:
        """Every caption showing at time t, lowest spot first: tuples (start, text[, name, color,
        track, end, layer]). People talking at once each have one, stacked by layer."""
        caps = (getattr(self, "display_captions", None) or self.captions)()
        muted = self.muted_names()
        if muted:
            caps = [c for c in caps if not (len(c) > 4 and c[4] in muted)]
        if not caps:
            return []
        starts = [c[0] for c in caps]
        i = bisect.bisect_right(starts, t) - 1
        if i < 0:
            return []
        if len(caps[i]) > 5:                     # captions with their own end times
            found = [c for c in caps[max(i - 6, 0):i + 1] if len(c) > 5 and c[0] <= t < c[5]]
            seen, out = set(), []
            for c in sorted(found, key=lambda c: (c[6] if len(c) > 6 else 0, c[0])):
                layer = c[6] if len(c) > 6 else 0
                if layer in seen:
                    continue                     # one per spot
                seen.add(layer)
                out.append(c)
            return out
        start = caps[i][0]
        nxt = caps[i + 1][0] if i + 1 < len(caps) else start + 6
        return [caps[i]] if t < min(nxt, start + 10) else []

    def _caption_at(self, t: float):
        """The main caption at time t (the one in the usual spot), or None."""
        caps = self._captions_at(t)
        return caps[0] if caps else None

    # ---------------------------------------------------------------- captions

    @staticmethod
    def _make_caption(parent, size: int) -> tk.Text:
        """A black strip that shows a caption centered, with the speaker's name in their color."""
        t = tk.Text(parent, background="black", foreground="white", font=ui_font(size, bold=True),
                    height=2, width=1, wrap="word", borderwidth=0, highlightthickness=0, cursor="arrow",
                    padx=10, pady=4, state="disabled", takefocus=0)
        t.tag_configure("center", justify="center")
        return t

    def _show_caption(self, caps):
        """Show the captions for this moment: one tuple, a list of them (people at once, stacked
        from the usual spot up), or None."""
        if caps and not isinstance(caps, list):
            caps = [caps]
        caps = caps or []
        if getattr(self, "_capedit", None) is not None:
            return                                 # being typed on: the editor stays as it is
        key = tuple(tuple(c[1:]) for c in caps) if caps else None
        if key == self._caption_shown:
            return
        self._caption_shown = key
        names = self.cap_style.get("names", True)
        speaker = self.cap_style.get("speaker", "name")
        if self._overlay_on():
            if self._boxwin_on():              # the app draws this style itself, over the video
                self._marquee("")
                self._show_boxwin(caps)
                return
            self._show_boxwin(None)
            # VLC draws one block of text: the stacked captions go on their own lines, top one first
            lines = []
            for cap in sorted(caps, key=lambda c: -(c[6] if len(c) > 6 else 0)):
                lines.append((f"{cap[2]}:  " if names and len(cap) > 3 and cap[2] else "") + cap[1])
            main = caps[0] if caps else None
            spk = main[3] if main and len(main) > 3 and main[3] else ""
            self._marquee_color(spk if speaker == "text" and spk and len(caps) == 1 else None)
            self._marquee("\n".join(lines))
            return
        self._show_boxwin(None)
        boxes = [self.caption] + ([self._fs[2]] if self._fs else [])
        from transcriber.captions import contrast_text
        for box in boxes:
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.configure(height=max(2, 2 * len(caps)) if caps else 2)
            for n, cap in enumerate(sorted(caps, key=lambda c: -(c[6] if len(c) > 6 else 0))):
                if n:
                    box.insert("end", "\n", "center")
                text = cap[1]
                spk = cap[3] if len(cap) > 3 and cap[3] else ""
                if speaker == "box" and spk:         # a box in the speaker's color behind the words
                    tag = "b" + spk.lstrip("#")
                    box.tag_configure(tag, background=spk, foreground=contrast_text(spk))
                    label = f"{cap[2]}:  " if names and cap[2] else ""
                    box.insert("end", f"\u2002{label}{text}\u2002", ("center", tag))
                else:
                    if names and len(cap) > 3 and cap[2]:
                        color = _lighten(cap[3])
                        tag = "n" + color.lstrip("#")
                        box.tag_configure(tag, foreground=color)
                        box.insert("end", cap[2] + ":  ", ("center", tag))
                    if speaker == "text" and spk:
                        tag = "t" + _lighten(spk).lstrip("#")
                        box.tag_configure(tag, foreground=_lighten(spk))
                        box.insert("end", text, ("center", tag))
                    else:
                        box.insert("end", text, "center")
            box.configure(state="disabled")

    def _marquee_color(self, color):
        """On-video captions in the speaker's color (None = the chosen text color)."""
        want = color or CAPTION_COLORS.get(self.cap_style.get("color", "white"), "#ffffff")
        if getattr(self, "_marq_color", None) == want or not self.available:
            return
        try:
            self.mp.video_set_marquee_int(vlc.VideoMarqueeOption.Color, int(want.lstrip("#"), 16))
            self._marq_color = want
        except Exception:
            pass

    # ---------------------------------------------------------------- on-video captions with a box in the speaker's color

    # VLC's on-video text has one background color for everything, and VLC's subtitle renderer
    # (libass) crashed on Windows when handed a styled subtitle file. So this style is drawn by the
    # app: a small borderless window that floats over the video (like the fullscreen button).

    def _boxwin_on(self) -> bool:
        return self._overlay_on() and self.cap_style.get("speaker") == "box"

    def _cap_window(self, layer: int = 0):
        """The floating caption box for a spot (0 the usual one; 1, 2 stacked above)."""
        wins = self.__dict__.setdefault("_capwins", {})
        if layer not in wins:
            win = tk.Toplevel(self)
            win.withdraw()
            win.overrideredirect(True)
            try:
                win.transient(self.winfo_toplevel())
            except tk.TclError:
                pass
            lbl = tk.Label(win, justify="center", padx=10, pady=3, borderwidth=0)
            lbl.pack()
            lbl.bind("<Button-1>", lambda e: self.toggle())
            lbl.bind("<Double-Button-1>", lambda e, layer=layer: self._edit_caption(layer))
            wins[layer] = (win, lbl)
        return wins[layer][0]

    def _show_boxwin(self, caps):
        """Show the captions in their boxes over the video (people at once: one box per spot), or
        hide them all with None."""
        from transcriber.captions import contrast_text
        if caps and not isinstance(caps, list):
            caps = [caps]
        self._captext = {}
        if not caps:
            for win, _lbl in self.__dict__.get("_capwins", {}).values():
                win.withdraw()
            return
        names = self.cap_style.get("names", True)
        for cap in caps:
            layer = cap[6] if len(cap) > 6 and cap[6] else 0
            self._cap_window(layer)
            win, lbl = self._capwins[layer]
            spk = cap[3] if len(cap) > 3 and cap[3] else "#111111"
            label = f"{cap[2]}:  " if names and len(cap) > 2 and cap[2] else ""
            self._captext[layer] = (label + str(cap[1]), spk)
            lbl.configure(text=self._captext[layer][0], background=spk, foreground=contrast_text(spk))
        for layer, (win, _lbl) in self._capwins.items():
            if layer not in self._captext and win.winfo_viewable():
                win.withdraw()
        self._place_boxwin()

    def _place_boxwin(self):
        """Keep the caption boxes over the bottom (or top) of the video as the window moves, each
        spot a caption's height above the one below."""
        wins = self.__dict__.get("_capwins", {})
        texts = self.__dict__.get("_captext") or {}
        if not wins:
            return
        if not isinstance(texts, dict):
            texts = {}
        target = self._fs[1] if self._fs else self.screen
        try:
            owner = self._fs[0] if self._fs else self.winfo_toplevel()
            if getattr(self, "_capowner", None) is not owner:     # stays just above the window it's on
                for win, _lbl in wins.values():
                    win.transient(owner)
                self._capowner = owner
            visible = (texts and self._boxwin_on() and target.winfo_viewable()
                       and self.winfo_toplevel().state() != "iconic")
            if not visible:
                for win, _lbl in wins.values():
                    if win.winfo_viewable():
                        win.withdraw()
                return
            w, h = target.winfo_width(), target.winfo_height()
            factor = caption_scale(self.cap_style)[0]
            px = max(int(h * factor * 0.8), 11)
            font = (CAPTION_FONT, -px, "bold")
            margin = int(h * 0.05)
            top = self.cap_style.get("position") == "top"
            step = 0                                          # where the next spot starts
            editing = (getattr(self, "_capedit", None) or {}).get("layer")
            for layer in sorted(texts):
                win, lbl = wins[layer]
                if layer == editing:                          # being typed on: the editor is there instead
                    if win.winfo_viewable():
                        win.withdraw()
                    ed = self._capedit["win"]
                    ed.update_idletasks()
                    step += ed.winfo_reqheight() + max(int(px * 0.3), 4)
                    continue
                if lbl.cget("font") != str(font):
                    lbl.configure(font=font)
                lbl.configure(wraplength=int(w * 0.85))
                win.update_idletasks()
                rw, rh = win.winfo_reqwidth(), win.winfo_reqheight()
                x = target.winfo_rootx() + (w - rw) // 2
                y = target.winfo_rooty() + (margin + step if top else h - rh - margin - step)
                geo = f"+{x}+{y}"
                if win.geometry().split("x")[-1].split("+", 1)[-1] != geo[1:] or not win.winfo_viewable():
                    win.geometry(geo)
                if not win.winfo_viewable():
                    win.deiconify()
                    win.lift(owner)
                step += rh + max(int(px * 0.3), 4)
        except tk.TclError:
            pass

    def refresh_captions(self, force: bool = False):
        """Captions changed (style, file, muted tracks): show the current one again."""
        if not self._boxwin_on():
            self._show_boxwin(None)
        before = self._caption_shown
        self._caption_shown = "redraw"
        if self.available and self.path:
            self._show_caption(self._captions_at(max(self.mp.get_time() / 1000, 0)))
            if self._caption_shown != before:
                self._repaint_paused()

    def _repaint_paused(self):
        """VLC draws its on-video text only with a new frame: while paused, show the same spot
        again so a changed caption (an unsaved edit) appears right away."""
        if not (self._overlay_on() and not self._boxwin_on()) or self._priming:
            return
        if getattr(self, "_repaint_job", None):
            self.after_cancel(self._repaint_job)

        def go():
            self._repaint_job = None
            try:
                if self.mp.get_state() == vlc.State.Paused:
                    self.mp.set_time(max(int(self.mp.get_time()), 0))
            except Exception:
                pass
        self._repaint_job = self.after(120, go)

    # ---------------------------------------------------------------- caption styles

    def _new_instance(self):
        """A VLC instance with the caption options; without them if this VLC doesn't have the
        caption (freetype) module, so the video still plays (captions then stay below it)."""
        inst = vlc.Instance(*self._vlc_args())
        if inst is None:
            self._no_marquee = True
            inst = vlc.Instance("--quiet")
        if inst is None:
            raise RuntimeError("VLC couldn't start")
        return inst

    def _vlc_args(self) -> list:
        """VLC's text look for captions drawn on the video (set when VLC starts)."""
        args = ["--quiet", "--no-video-title-show", "--no-sub-autodetect-file", "--no-snapshot-preview",
                "--sub-source=marq", f"--freetype-font={CAPTION_FONT}", "--freetype-background-color=0"]
        bg = self.cap_style.get("background", "box")
        args += {"outline": ["--freetype-outline-thickness=4", "--freetype-background-opacity=0"],
                 "shadow": ["--freetype-outline-thickness=0", "--freetype-shadow-opacity=200",
                            "--freetype-shadow-distance=0.07", "--freetype-background-opacity=0"],
                 "box": ["--freetype-outline-thickness=0", "--freetype-background-opacity=150"],
                 "solid": ["--freetype-outline-thickness=0", "--freetype-background-opacity=255"]}.get(bg, [])
        return args

    def _overlay_on(self) -> bool:
        """Captions go on the picture: 'on the video' style and there is a picture to put them on."""
        return (self.cap_style.get("mode") == "overlay" and self.available and bool(self.path)
                and not getattr(self, "_no_marquee", False)
                and not getattr(self, "_audio_cover", False) and not self._tiles and not self.audio_only_view)

    def set_caption_style(self, style: dict):
        old_args = self._vlc_args()
        self.cap_style = {**DEFAULT_CAPTIONS, **(style or {})}
        if self.available and self._vlc_args() != old_args:
            self._restart_vlc()
        self._marq_ready = False
        self._marq_color = None
        self._layout_caption()
        self._caption_shown = "redraw"
        self.refresh_captions()
        size = caption_scale(self.cap_style)[1]
        color = CAPTION_COLORS.get(self.cap_style.get("color", "white"), "#ffffff")
        for box in [self.caption] + ([self._fs[2]] if self._fs else []):
            box.configure(font=ui_font(size if box is self.caption else size + 6, bold=True), foreground=color,
                          height=3 if size_percent(self.cap_style) >= 200 else 2)   # big text: room for more

    def _layout_caption(self):
        """The strip under the video only when captions aren't drawn on the video."""
        below = not self._overlay_on()
        if below and not self.caption.winfo_ismapped():
            self.caption.pack(fill="x", before=self._controls)
        elif not below and self.caption.winfo_ismapped():
            self.caption.pack_forget()
        if not below:
            self.caption.configure(state="normal")
            self.caption.delete("1.0", "end")
            self.caption.configure(state="disabled")
        else:
            self._marquee("")
        if self._fs:
            cap = self._fs[2]
            if below and not cap.winfo_ismapped():
                cap.pack(side="bottom", fill="x", pady=(0, 18), before=self._fs[1])
            elif not below and cap.winfo_ismapped():
                cap.pack_forget()

    def _marquee(self, text: str):
        if not self.available:
            return
        try:
            if text and not self._marq_ready:
                self._setup_marquee()
            self.mp.video_set_marquee_string(vlc.VideoMarqueeOption.Text, text or " ")
        except Exception:
            pass

    def _setup_marquee(self):
        """Size and place the on-video captions for this video's resolution."""
        w, h = self.mp.video_get_size() or (0, 0)
        if not h:
            return
        M = vlc.VideoMarqueeOption
        factor = caption_scale(self.cap_style)[0]
        color = int(CAPTION_COLORS.get(self.cap_style.get("color", "white"), "#ffffff")[1:], 16)
        top = self.cap_style.get("position") == "top"
        self.mp.video_set_marquee_int(M.Enable, 1)
        self.mp.video_set_marquee_int(M.Size, max(int(h * factor), 10))
        self.mp.video_set_marquee_int(M.Position, 4 if top else 8)
        self.mp.video_set_marquee_int(M.Y, int(h * 0.05))
        self.mp.video_set_marquee_int(M.Color, color)
        self.mp.video_set_marquee_int(M.Opacity, 255)
        self.mp.video_set_marquee_int(M.Timeout, 0)
        self.mp.video_set_marquee_int(M.Refresh, 100)
        self._marq_ready = True

    def _restart_vlc(self):
        """New text look = a new VLC instance (it reads those settings at start). Keeps your place."""
        pos = max(self.mp.get_time(), 0) if self.path else 0
        playing = self.mp.is_playing() and not self._priming
        muted = self.mp.audio_get_mute()
        self._drop_extras()
        try:
            self.mp.stop()
            self.mp.release()
            self.instance.release()
        except Exception:
            pass
        self.instance = self._new_instance()
        self.mp = self.instance.media_player_new()
        self.mp.video_set_mouse_input(False)
        self.mp.video_set_key_input(False)
        self._attach(self._fs[1] if self._fs else None)
        if self.path:
            self.mp.set_media(self.instance.media_new(self.path))
            self.mp.audio_set_mute(True)
            self.mp.play()
            self._track_applied = False

            def resume(tries=0):
                if self.mp.get_state() == vlc.State.Playing or tries > 40:
                    self.mp.set_time(int(pos))
                    if not playing:
                        self.mp.set_pause(1)
                    self.mp.audio_set_mute(muted)
                    self._sync_extras()
                    self._marq_color = None
                    self.refresh_captions(force=True)
                else:
                    self.after(50, lambda: resume(tries + 1))
            self.after(50, resume)

    # ---------------------------------------------------------------- fullscreen

    def _make_fs_button(self):
        """A small see-through button over the top right of the video, shown while the mouse is
        over it. It's its own little window so it stays above VLC's picture."""
        self._fs_btn = tk.Toplevel(self)
        self._fs_btn.withdraw()
        self._fs_btn.overrideredirect(True)
        try:
            self._fs_btn.attributes("-alpha", 0.55)
        except tk.TclError:
            pass
        fam = icon_family(self)
        self._fs_label = tk.Label(self._fs_btn, text=glyph(self, "fullscreen"), background="#000000",
                                  foreground="#ffffff", font=(fam, 11) if fam else ui_font(11),
                                  padx=7, pady=4, cursor="hand2")
        self._fs_label.pack()
        self._fs_label.bind("<Button-1>", lambda e: self.toggle_fullscreen())
        self._fs_label.bind("<Enter>", lambda e: self._fs_alpha(0.85))
        self._fs_label.bind("<Leave>", lambda e: self._fs_alpha(0.55))

    def _fs_alpha(self, a):
        try:
            self._fs_btn.attributes("-alpha", a)
        except tk.TclError:
            pass

    def _hover_check(self):
        """Show the fullscreen button only while the mouse is over the video."""
        try:
            target = self._fs[1] if self._fs else self.screen
            show = bool(self.available and self.path and target.winfo_viewable()
                        and (self._fs or not (self.overlay.winfo_ismapped() or self.call.winfo_ismapped()
                                              or self.audio_only_view)))
            if show:
                px, py = self.winfo_pointerxy()
                x0, y0 = target.winfo_rootx(), target.winfo_rooty()
                w, h = target.winfo_width(), target.winfo_height()
                show = x0 <= px < x0 + w and y0 <= py < y0 + h
            if show:
                self._fs_label.configure(text=glyph(self, "exitfull" if self._fs else "fullscreen"))
                self._fs_btn.update_idletasks()
                bw = self._fs_btn.winfo_reqwidth()
                self._fs_btn.geometry(f"+{x0 + w - bw - 10}+{y0 + 10}")
                if not self._fs_btn.winfo_viewable():
                    self._fs_btn.deiconify()
                self._fs_btn.lift()
            elif self._fs_btn.winfo_viewable():
                self._fs_btn.withdraw()
        except tk.TclError:
            pass
        self._place_boxwin()             # the caption box follows the video when the window moves
        self.after(120, self._hover_check)

    def _video_double(self, e):
        """Double-click on the video: edit the caption when it's on the caption (drawn on the
        video), otherwise fullscreen."""
        if self._on_caption(e):
            self._edit_caption()
        else:
            self.toggle_fullscreen()

    def _on_caption(self, e) -> bool:
        if not (self.on_caption_edit and self._overlay_on() and self.available and self.path):
            return False
        if not isinstance(self._caption_shown, tuple):          # no caption showing
            return False
        h = max(e.widget.winfo_height(), 1)
        y = e.y_root - e.widget.winfo_rooty()
        band = 0.35 * h                                         # where the caption sits (near the edge)
        return y <= band if self.cap_style.get("position") == "top" else y >= h - band

    def _edit_caption(self, layer: int = 0):
        """Double-click on a caption: the app gets the caption showing in that spot (paused, out of
        fullscreen) and opens it for typing right there (start_caption_edit)."""
        if not self.on_caption_edit or not self.available or not self.path:
            return
        if getattr(self, "_capedit", None) is not None:
            return
        caps = self._captions_at(max(self.mp.get_time() / 1000, 0))
        cap = next((c for c in caps if (c[6] if len(c) > 6 else 0) == layer), caps[0] if caps else None)
        if not cap:
            return
        if self.mp.is_playing():
            self.mp.set_pause(1)
        if self._fs is not None:
            self.toggle_fullscreen()
        self.after_idle(lambda: self.on_caption_edit(cap))

    # ---------------------------------------------------------------- typing on a caption

    def start_caption_edit(self, cap, text: str, done: Callable[[Optional[str]], None]):
        """Type on the caption itself: a box over the video where the caption sits, with the line's
        text in it. Enter keeps the new text (done(text)), Esc throws it away (done(None));
        clicking elsewhere keeps it too."""
        from transcriber.captions import contrast_text
        self.end_caption_edit(commit=False)
        layer = cap[6] if len(cap) > 6 and cap[6] else 0
        spk = cap[3] if len(cap) > 3 and cap[3] else ""
        box = self._boxwin_on() and spk
        bg = spk if box else "#161616"
        fg = contrast_text(spk) if box else CAPTION_COLORS.get(self.cap_style.get("color", "white"), "#ffffff")
        win = tk.Toplevel(self)
        win.withdraw()
        win.overrideredirect(True)
        try:
            win.transient(self.winfo_toplevel())
        except tk.TclError:
            pass
        t = tk.Text(win, wrap="word", borderwidth=0, highlightthickness=2, highlightbackground="#4cc2ff",
                    highlightcolor="#4cc2ff", background=bg, foreground=fg, insertbackground=fg,
                    padx=10, pady=4, undo=True, height=1)
        t.pack(fill="both", expand=True)
        t.insert("1.0", text)
        t.tag_configure("center", justify="center")
        t.tag_add("center", "1.0", "end")
        self._capedit = {"win": win, "text": t, "layer": layer, "done": done, "orig": text}
        # the caption underneath goes away while its words are typed on
        if self._boxwin_on():
            wins = self.__dict__.get("_capwins", {})
            if layer in wins:
                wins[layer][0].withdraw()
        elif self._overlay_on():
            self._marquee("")
        t.bind("<Return>", lambda e: self.end_caption_edit(True) or "break")
        t.bind("<KP_Enter>", lambda e: self.end_caption_edit(True) or "break")
        t.bind("<Escape>", lambda e: self.end_caption_edit(False) or "break")
        t.bind("<FocusOut>", lambda e: self.after(80, lambda: self.end_caption_edit(True)))
        t.bind("<Tab>", lambda e: "break")
        t.bind("<KeyRelease>", lambda e: (self._size_capedit(), t.tag_add("center", "1.0", "end")))
        self._size_capedit()
        win.deiconify()
        win.lift()
        t.focus_force()
        t.mark_set("insert", "end-1c")
        t.see("insert")

    def _size_capedit(self):
        """Fit the editor to its words, where the caption sits over the video."""
        ed = getattr(self, "_capedit", None)
        if ed is None:
            return
        try:
            target = self._fs[1] if self._fs else self.screen
            w, h = target.winfo_width(), target.winfo_height()
            overlay = self._overlay_on()
            if overlay:
                px = max(int(h * caption_scale(self.cap_style)[0] * 0.8), 11)
                font = (CAPTION_FONT, -px, "bold")
            else:
                size = caption_scale(self.cap_style)[1]
                font = ui_font(size, bold=True)
                px = int(size * 1.6)
            t = ed["text"]
            f = tkfont.Font(font=font)
            width_px = int(w * 0.85)
            chars = max(int(width_px / max(f.measure("n"), 1)), 10)
            words = t.get("1.0", "end-1c")
            lines, cur = 1, 0
            for word in words.split():
                if cur and cur + 1 + len(word) > chars:
                    lines, cur = lines + 1, len(word)
                else:
                    cur += (1 if cur else 0) + len(word)
            t.configure(font=font, width=chars, height=min(max(lines, 1), 5))
            win = ed["win"]
            win.update_idletasks()
            rw, rh = win.winfo_reqwidth(), win.winfo_reqheight()
            x = target.winfo_rootx() + (w - rw) // 2
            if overlay:
                margin = int(h * 0.05)
                step = ed["layer"] * (rh + max(int(px * 0.3), 4))
                y = target.winfo_rooty() + (margin + step if self.cap_style.get("position") == "top"
                                            else h - rh - margin - step)
            else:                                    # the strip below the video: over the strip
                strip = self.caption
                y = strip.winfo_rooty() + max((strip.winfo_height() - rh) // 2, 0)
            win.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass

    def end_caption_edit(self, commit: bool = True):
        """Close the caption editor; commit: hand the new text to the app (None if unchanged)."""
        ed = getattr(self, "_capedit", None)
        if ed is None:
            return
        self._capedit = None
        try:
            new = " ".join(ed["text"].get("1.0", "end-1c").split())
            ed["win"].destroy()
        except tk.TclError:
            new = None
        self._caption_shown = "redraw"
        if commit and new is not None and new != " ".join(ed["orig"].split()):
            ed["done"](new)
        else:
            ed["done"](None)
        self.refresh_captions()

    def toggle_fullscreen(self):
        if not self.available or not self.path:
            return
        pos = max(self.mp.get_time(), 0)
        playing = self.mp.is_playing() and not self._priming
        if self._fs is None:
            win = tk.Toplevel(self, background="black")
            win.title("Preview")
            # Fill the monitor the app is on.
            top = self.winfo_toplevel()
            win.geometry(f"+{top.winfo_rootx() + 20}+{top.winfo_rooty() + 20}")
            win.update_idletasks()
            win.attributes("-fullscreen", True)
            video = tk.Frame(win, background="black")
            cap = self._make_caption(win, 18)
            cap.configure(height=2)
            size = caption_scale(self.cap_style)[1]
            cap.configure(font=ui_font(size + 6, bold=True),
                          foreground=CAPTION_COLORS.get(self.cap_style.get("color", "white"), "#ffffff"))
            if not self._overlay_on():
                cap.pack(side="bottom", fill="x", pady=(0, 18))
            video.pack(fill="both", expand=True)
            for w in (win, video, cap):
                w.bind("<Escape>", lambda e: self.toggle_fullscreen())
                w.bind("<F11>", lambda e: self.toggle_fullscreen())
                w.bind("<space>", lambda e: self.toggle())
            video.bind("<Button-1>", lambda e: self.toggle())
            video.bind("<Double-Button-1>", self._video_double)
            cap.bind("<Double-Button-1>", lambda e: self._edit_caption())
            win.protocol("WM_DELETE_WINDOW", self.toggle_fullscreen)
            win.update()
            if win.winfo_width() < 200:     # fullscreen not supported here: cover the screen instead
                win.geometry(f"{win.winfo_screenwidth()}x{win.winfo_screenheight()}+0+0")
                win.update()
            win.focus_force()
            self._fs = (win, video, cap)
            surface = video
        else:
            win = self._fs[0]
            self._fs = None
            surface = self.surface
            win.destroy()
        self._caption_shown = "redraw"
        self._rehost(surface, pos, playing)

    def _rehost(self, surface, pos_ms: int, playing: bool):
        """Move the picture to another frame. VLC can't switch frames mid-play, so restart at the
        same spot (muted until it's back, so there's no blip of sound from the start)."""
        muted = self.mp.audio_get_mute()
        self.mp.stop()
        self._attach(surface)
        self.mp.audio_set_mute(True)
        self.mp.play()
        self._track_applied = False

        def resume(tries=0):
            if self.mp.get_state() == vlc.State.Playing or tries > 40:
                self.mp.set_time(int(pos_ms))
                if not playing:
                    self.mp.set_pause(1)
                self.mp.audio_set_mute(muted)
                self.after(100, lambda: self._follow_extras(force=True))
                self._marq_color = None
                self.refresh_captions(force=True)
            else:
                self.after(50, lambda: resume(tries + 1))
        self.after(50, resume)

    def _tick(self):
        if self.available and self.path:
            try:
                length = self.mp.get_length() / 1000
                if length > 0:
                    self.length = length
                t = max(self.mp.get_time() / 1000, 0)
                if self.length and not self._dragging:
                    self.scale.set(t / self.length)
                self.time_var.set(f"{fmt_time(t)} / {fmt_time(self.length)}")
                playing = self.mp.is_playing() and not self._priming
                muted = self.mp.audio_get_mute() and not self._priming
                self.play_btn.configure(text=glyph(self, "pause" if playing else "play"))
                self.mute_btn.configure(text=glyph(self, "mute" if muted else "volume"))
                self.play_tip.set_text("Pause" if playing else "Play")
                self.mute_tip.set_text("Unmute" if muted else "Mute")
                self._show_caption(self._captions_at(t))
                if self._tiles or getattr(self, "_note_view", False):
                    self._draw_call()
                if self.on_time:
                    self.on_time(t, playing)
                self._apply_track()
                self._apply_volumes(t)
                if playing and abs(self.mp.get_rate() - self.rate) > 0.01:
                    self.mp.set_rate(self.rate)
                self._follow_extras()
            except Exception:
                pass
        elif self.on_time:
            self.on_time(None, False)
        self.after(200, self._tick)
