"""The subtitle timeline: a strip along the bottom of the window, like a video editor's.

One lane per track (or one for the whole recording). Each lane shows the audio's waveform on top
and the transcript's lines as blocks underneath, lined up in time. Drag a block to move it, drag
either edge to change when it starts or ends. Edges snap to where the voice starts and stops, to
the neighbouring lines and to the playhead (hold Alt to place freely). A red playhead follows the
player; click anywhere to jump there. Scroll (or hold the wheel and drag) to move along, Ctrl+scroll to zoom.

Three tools, like an editor's: Select (move and resize lines), Snip (click a line to split it in two
between words; a dashed line shows where) and Range (drag over a stretch to play just that part, or
export it as a clip).

The widget knows nothing about files: the app gives it lanes and is told about every change
through callbacks (on_change, on_seek, on_select, on_edit_text, on_snip)."""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font as tkfont, ttk
from typing import Callable, Optional

ENV_RATE = 50              # waveform values per second (session.envelope)
GUTTER = 92                # the lane names on the left
RULER_H = 22
HANDLE = 6                 # how close (pixels) to a block's edge grabs the edge
SNAP_PX = 9                # how close (pixels) an edge must come to snap
MIN_LEN = 0.2              # shortest a line can be (seconds)
MAX_PPS = 400.0            # most zoomed in: pixels per second


def fmt(t: float, fine: bool = True) -> str:
    t = max(t, 0.0)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    if fine:
        return (f"{int(h)}:{int(m):02d}:{s:05.2f}" if h else f"{int(m)}:{s:05.2f}")
    return f"{int(h)}:{int(m):02d}:{int(s):02d}" if h else f"{int(m)}:{int(s):02d}"


def alt_held(e) -> bool:
    """Alt (Option on a Mac). On Windows bit 0x8 is NumLock, not Alt."""
    if sys.platform == "win32":
        return bool(e.state & 0x20000)
    if sys.platform == "darwin":
        return bool(e.state & 0x10)
    return bool(e.state & 0x8)


def command_held(e) -> bool:
    """Ctrl, Alt (or Command / Option on a Mac): a shortcut, not a plain key."""
    return bool(e.state & 0x4) or alt_held(e) or (sys.platform == "darwin" and bool(e.state & 0x8))


_MIX_CACHE: dict = {}


def _mix(a: str, b: str, t: float) -> str:
    """Color a blended toward b by t (0..1). (A handful of colors, asked for thousands of times.)"""
    key = (a, b, t)
    hit = _MIX_CACHE.get(key)
    if hit is None:
        if len(_MIX_CACHE) > 2000:
            _MIX_CACHE.clear()
        hit = _MIX_CACHE[key] = _mix_colors(a, b, t)
    return hit


def _mix_colors(a: str, b: str, t: float) -> str:
    try:
        ra, ga, ba = (int(a[i:i + 2], 16) for i in (1, 3, 5))
        rb, gb, bb = (int(b[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, TypeError):
        return a
    return "#%02x%02x%02x" % (round(ra + (rb - ra) * t), round(ga + (gb - ga) * t), round(ba + (bb - ba) * t))


def speech_edges(env, threshold_db: float = 22.0, min_gap: float = 0.25):
    """(onsets, offsets) in seconds from a loudness envelope: where talking starts and stops."""
    import numpy as np
    if env is None or not len(env):
        return [], []
    loud = env > float(np.percentile(env, 95)) - threshold_db
    gap = int(min_gap * ENV_RATE)
    on, off, quiet = [], [], gap
    for i, v in enumerate(loud):
        if v:
            if quiet >= gap:
                on.append(i / ENV_RATE)
            quiet = 0
        else:
            quiet += 1
            if quiet == gap:
                off.append((i - gap + 1) / ENV_RATE)
    if len(loud) and loud[-1]:
        off.append(len(loud) / ENV_RATE)
    return on, off


class Timeline(ttk.Frame):
    def __init__(self, parent, palette: Callable[[], dict], dark: Callable[[], bool], scale: float = 1.0,
                 on_change: Optional[Callable] = None, on_seek: Optional[Callable] = None,
                 on_select: Optional[Callable] = None, on_edit_text: Optional[Callable] = None,
                 on_snip: Optional[Callable] = None, snip_at: Optional[Callable] = None,
                 on_delete: Optional[Callable] = None, on_menu: Optional[Callable] = None,
                 on_copy: Optional[Callable] = None, on_paste: Optional[Callable] = None,
                 on_layer: Optional[Callable] = None, on_range: Optional[Callable] = None,
                 on_play_range: Optional[Callable] = None, on_export_range: Optional[Callable] = None,
                 keys: Optional[Callable[[], dict]] = None, tooltip=None, info_icon=None, **kw):
        super().__init__(parent, **kw)
        self.palette, self.dark, self.scale = palette, dark, scale
        self.on_change, self.on_seek, self.on_select, self.on_edit_text = on_change, on_seek, on_select, on_edit_text
        # on_snip(key, t): split that line at t. snip_at(key, t) -> (t between words, left, right) or None
        self.on_snip, self.snip_at = on_snip, snip_at
        self.on_delete = on_delete                  # on_delete(key): the Delete key on the selected line
        self.on_menu = on_menu                      # on_menu(key, x_root, y_root, in_range): right-click on a line (key None: on the marked stretch)
        self.on_copy, self.on_paste = on_copy, on_paste      # on_copy(key), on_paste(): Ctrl+C / Ctrl+V
        self.on_layer = on_layer                    # on_layer(key, layer): a block dragged to another caption spot
        self.on_range = on_range                    # on_range(start, end) or on_range(None, None): the marked stretch
        self.on_play_range, self.on_export_range = on_play_range, on_export_range   # (start, end)
        self.range: Optional[tuple] = None          # the marked stretch (start, end), or None
        self._seek_job = None                       # scrubbing: the player is told at most ~25 times a second
        self._seek_pending: Optional[float] = None
        self.keys = keys or (lambda: {"select": "s", "snip": "b", "range": "r"})
        self.tool = "select"
        self.lanes: list = []          # [{"name", "blocks": [...], "env", "env_offset", "edges"}]
        self.duration = 60.0
        self.t0, self.pps = 0.0, 20.0
        self.time: Optional[float] = None
        self.playing = False
        self.selected = None            # a block's key
        self._drag = None
        self._doc = None
        self._font = tkfont.Font(family=tkfont.nametofont("TkDefaultFont").actual("family"),
                                 size=max(int(9 * scale), 8))
        self._fit_cache: dict = {}
        self._fitted = True             # showing the whole recording (kept on resize until you zoom/scroll)

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=(4, 2))
        ttk.Label(bar, text="Timeline", font=(self._font.actual("family"), max(int(10 * scale), 9), "bold")
                  ).pack(side="left")
        if info_icon:
            info_icon(bar, "Drag a line to move it, or its edges to change when it starts and ends. Edges snap "
                           "to where the voice starts and stops (hold Alt to place freely). Click to jump there, "
                           "double-click to edit the text, Delete removes the selected line, Ctrl+C / Ctrl+V copy it to the playhead (Ctrl+Z undoes). Drag a line up to stack its caption above another person's (people talking at once); drag it back down for the usual spot. Snip tool: click a line to split it in two. Range tool: drag over a stretch to play just that part or export it as a clip. "
                           "A faint tail after a line is how much longer its caption stays on screen so it can be read; drag the line's edge to set the end exactly. "
                           "Scroll, or hold the scroll wheel and drag, to move along; Ctrl+scroll to zoom. "
                           "Moves are saved with Save, like other edits.").pack(side="left", padx=6)
        # compact controls: the timeline can sit in a narrow column under the video
        small = ttk.Style(self)
        small.configure("Tiny.TButton", padding=(4, 1))
        small.configure("Tiny.Accent.TButton", padding=(4, 1))
        ttk.Button(bar, text="Fit", width=3, style="Tiny.TButton", command=self.fit).pack(side="right")
        ttk.Button(bar, text="+", width=2, style="Tiny.TButton", command=lambda: self.zoom(1.5)).pack(side="right", padx=(0, 2))
        ttk.Button(bar, text="\u2212", width=2, style="Tiny.TButton", command=lambda: self.zoom(1 / 1.5)).pack(side="right", padx=(0, 2))
        self.snap_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Snap", variable=self.snap_var).pack(side="right", padx=(0, 6))
        # the tools (keys from Settings; the tooltips say which)
        self._tool_btns = {}
        tips = {"select": "Select (move and resize lines)", "snip": "Snip (click a line to split it in two)",
                "range": "Range (drag over a stretch to play or export just that part)"}
        for tool, text in (("range", "\u2194"), ("snip", "\u2702"), ("select", "\u2196")):
            b = ttk.Button(bar, text=text, width=2, style="Tiny.TButton", command=lambda t=tool: self.set_tool(t))
            b.pack(side="right", padx=(0, 2 if tool == "select" else 8 if tool == "range" else 2))
            self._tool_btns[tool] = b
            if tooltip:
                tooltip(b, lambda t=tool: f"{tips[t]}: {self.keys().get(t, '?').upper()}")
        self._tool_btns["select"].configure(style="Tiny.Accent.TButton")
        # The middle of the bar never asks for room (a long info line, or the range controls, must
        # not widen the whole column and re-lay out the canvas on every mouse move).
        mid = ttk.Frame(bar, height=max(int(24 * scale), 22))
        mid.pack_propagate(False)
        # the marked stretch: its times, play it, export it as a clip, clear it (shown while one is marked)
        self._range_bar = ttk.Frame(mid)
        for text, cmd, tip in (("\u25b6", self._play_range, "Play just this stretch in the player (Shift+Space)"),
                               ("Clip", self._export_range, "Export this stretch as its own video or audio file"),
                               ("\u00d7", self.clear_range, "Clear the marked stretch (Esc)")):
            rb = ttk.Button(self._range_bar, text=text, width=2 if len(text) == 1 else 4, style="Tiny.TButton", command=cmd)
            rb.pack(side="left", padx=(0, 2))
            if tooltip:
                tooltip(rb, tip)
        # (packed last so a long line of info never pushes the buttons out of the way)
        mid.pack(side="left", padx=(10, 0), fill="x", expand=True)
        self.info = ttk.Label(mid, text="", style="Muted.TLabel", width=1)
        self.info.pack(side="left", fill="x", expand=True)

        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, height=int(140 * scale))
        self.canvas.pack(fill="both", expand=True)
        self.hbar = ttk.Scrollbar(self, orient="horizontal", command=self._on_scrollbar)
        self.hbar.pack(fill="x")
        c = self.canvas
        self._size = (0, 0)
        c.bind("<Configure>", self._on_configure)
        c.bind("<Motion>", self._on_motion)
        c.bind("<Leave>", lambda e: (c.configure(cursor=""), self._show_info(None)))
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Double-Button-1>", self._on_double)
        for seq in ("<MouseWheel>", "<Shift-MouseWheel>", "<Control-MouseWheel>", "<Button-4>", "<Button-5>",
                    "<Control-Button-4>", "<Control-Button-5>"):
            c.bind(seq, self._on_wheel)
        # Hold the scroll wheel (middle button) and drag to move along, like a map.
        middle = "3" if sys.platform == "darwin" else "2"
        c.bind(f"<ButtonPress-{middle}>", self._pan_start)
        c.bind(f"<B{middle}-Motion>", self._pan_move)
        c.bind(f"<ButtonRelease-{middle}>", self._pan_end)
        c.bind("<Left>", lambda e: self._nudge(-0.5 if e.state & 1 else -0.1))
        c.bind("<Right>", lambda e: self._nudge(0.5 if e.state & 1 else 0.1))
        c.bind("<Escape>", lambda e: self._cancel_drag() if self._drag else
               self.clear_range() if self.range else self.set_tool("select"))
        c.bind("<Shift-space>", lambda e: self._play_range() or "break")
        for seq in ("<Delete>", "<BackSpace>"):
            c.bind(seq, self._delete_selected)
        from transcriber.fonts import bind_keys
        for k in ("c", "C"):
            bind_keys(c, k, lambda e: (self.on_copy(self.selected) if self.on_copy and self.selected is not None
                                       else None) or "break")
        for k in ("v", "V"):
            bind_keys(c, k, lambda e: (self.on_paste() if self.on_paste else None) or "break")
        c.bind("<Button-2>" if sys.platform == "darwin" else "<Button-3>", self._on_right_click)
        if sys.platform == "darwin":
            c.bind("<Control-Button-1>", self._on_right_click)

    def _on_configure(self, e):
        """The canvas was laid out again: redraw only if its size really changed (Tk also sends this
        when something next to it is re-packed, which would be a full redraw for nothing)."""
        size = (e.width, e.height)
        if size == self._size:
            return
        self._size = size
        if self._fitted:
            self.fit()
        else:
            self.redraw()

    def set_tool(self, tool: str):
        """Select, Snip or Range."""
        if tool not in ("select", "snip", "range"):
            return
        self.tool = tool
        for name, b in self._tool_btns.items():
            b.configure(style="Tiny.Accent.TButton" if name == tool else "Tiny.TButton")
        self.canvas.delete("snipline")
        self.canvas.configure(cursor="")
        self.info.configure(text={"snip": "Snip: click a line where it should split",
                                  "range": "Range: drag over the part to play or export"}.get(tool, ""))

    # ---------------------------------------------------------------- the marked stretch

    def set_range(self, start: Optional[float], end: Optional[float] = None, tell: bool = True):
        """Mark a stretch (or clear it with None). Shown across the lanes; Play and Clip act on it."""
        if start is None or end is None or abs(end - start) < 0.05:
            self.range = None
            self._range_bar.pack_forget()
        else:
            a, b = min(start, end), max(start, end)
            self.range = (max(0.0, a), min(b, max(self.duration, a + 0.05)))
            self.info.configure(text=f"{fmt(self.range[0])} \u2013 {fmt(self.range[1])}  "
                                     f"({self.range[1] - self.range[0]:.1f} s marked)")
            if not self._range_bar.winfo_manager():
                self._range_bar.pack(side="right", padx=(0, 6))
        if tell and self.on_range:
            self.on_range(*(self.range or (None, None)))
        self._draw_range()

    def _draw_range(self):
        """The marked stretch: a band over the lanes and a bar on the ruler (its own items, so marking
        one by dragging doesn't redraw the whole timeline on every mouse move)."""
        c = self.canvas
        c.delete("range")
        if not self.range:
            return
        W, H = c.winfo_width(), c.winfo_height()
        x0, x1 = max(self._x(self.range[0]), GUTTER), min(self._x(self.range[1]), W)
        if x1 <= x0:
            return
        col = self._colors()
        c.create_rectangle(x0, RULER_H, x1, H, fill=col["sel"], stipple="gray25", outline="", tags=("range",))
        for x in (x0, x1):
            c.create_line(x, RULER_H, x, H, fill=col["sel"], dash=(4, 3), tags=("range",))
        c.create_rectangle(x0, RULER_H - 5, x1, RULER_H, fill=col["sel"], outline="", tags=("range",))
        c.tag_raise("playhead")

    def clear_range(self):
        self.set_range(None)
        return "break"

    def _play_range(self):
        if self.range and self.on_play_range:
            self.on_play_range(*self.range)

    def _export_range(self):
        if self.range and self.on_export_range:
            self.on_export_range(*self.range)

    # ---------------------------------------------------------------- data in

    def set_lanes(self, lanes: list, duration: float, doc=None):
        """lanes = [{"name", "blocks": [{"key", "start", "end", "text", "name", "color", "edited"}]}].
        doc: what's shown (a new one fits the view; the same one keeps where you were)."""
        old_env = {ln["name"]: (ln.get("env"), ln.get("env_offset", 0.0), ln.get("edges")) for ln in self.lanes}
        self.lanes = []
        for ln in lanes:
            env, off, edges = old_env.get(ln["name"], (None, 0.0, None)) if doc == self._doc else (None, 0.0, None)
            blocks = sorted(ln["blocks"], key=lambda b: (b["start"], b["end"]))
            names = {b.get("name") for b in blocks}
            self.lanes.append({**ln, "blocks": blocks, "env": env, "env_offset": off, "edges": edges,
                               "one_voice": len(names) <= 1})       # then blocks don't repeat the name
        ends = [b["end"] for ln in self.lanes for b in ln["blocks"]]
        self.duration = max([duration or 0.0] + ends + [10.0])
        if doc != self._doc:
            self._doc = doc
            self.selected = None
            if self.range:
                self.set_range(None, tell=False)
            self.after_idle(self.fit)
        self.redraw()

    def set_envelope(self, lane_name: str, env, offset: float = 0.0):
        for ln in self.lanes:
            if ln["name"] == lane_name:
                ln["env"], ln["env_offset"] = env, offset
                ln.pop("norm", None)
                on, off = speech_edges(env)
                ln["edges"] = ([t + offset for t in on], [t + offset for t in off])
        self.redraw_soon()

    def redraw_soon(self):
        """Redraw once, shortly: several changes in a row (waveforms arriving) draw one picture."""
        if getattr(self, "_redraw_job", None) is None:
            self._redraw_job = self.after(40, self._redraw_now)

    def _redraw_now(self):
        self._redraw_job = None
        self.redraw()

    def set_time(self, t: Optional[float], playing: bool = False):
        self.time, self.playing = t, playing
        if t is not None and playing and self._drag is None:
            w = self._view_w()
            x = self._x(t)
            if x > GUTTER + w * 0.92 or x < GUTTER:          # keep the playhead in view
                self.t0 = max(0.0, min(t - w / self.pps * 0.1, self._max_t0()))
                self.redraw()
                return
        self._draw_playhead()

    def select(self, key, reveal: bool = True):
        self.selected = key
        if reveal and key is not None:
            b = self._block(key)
            if b:
                lo, hi = self.t0, self.t0 + self._view_w() / self.pps
                if b[1]["start"] < lo or b[1]["end"] > hi:
                    self.t0 = max(0.0, min(b[1]["start"] - (hi - lo) * 0.2, self._max_t0()))
        self.redraw()

    # ---------------------------------------------------------------- view

    def _view_w(self) -> int:
        return max(self.canvas.winfo_width() - GUTTER, 50)

    def _x(self, t: float) -> float:
        return GUTTER + (t - self.t0) * self.pps

    def _t(self, x: float) -> float:
        return self.t0 + (x - GUTTER) / self.pps

    def _min_pps(self) -> float:
        return max(self._view_w() / max(self.duration, 1.0) * 0.95, 0.02)

    def _max_t0(self) -> float:
        return max(self.duration - self._view_w() / self.pps, 0.0)

    def fit(self):
        self.pps = self._min_pps()
        self.t0 = 0.0
        self._fitted = True
        self.redraw()

    def zoom(self, factor: float, at_x: Optional[float] = None):
        self._fitted = False
        at_x = GUTTER + self._view_w() / 2 if at_x is None else at_x
        anchor = self._t(at_x)
        self.pps = min(max(self.pps * factor, self._min_pps()), MAX_PPS)
        self.t0 = max(0.0, min(anchor - (at_x - GUTTER) / self.pps, self._max_t0()))
        self.redraw()

    def _on_wheel(self, e):
        up = getattr(e, "num", 0) == 4 or getattr(e, "delta", 0) > 0
        if e.state & 0x0004:                                   # Ctrl: zoom around the mouse
            self.zoom(1.25 if up else 0.8, e.x)
        else:
            step = self._view_w() / self.pps * 0.15
            self.t0 = max(0.0, min(self.t0 + (-step if up else step), self._max_t0()))
            self._fitted = False
            self.redraw()
        return "break"

    def _pan_start(self, e):
        self._pan = (e.x, self.t0)
        self.canvas.configure(cursor="fleur")
        return "break"

    def _pan_move(self, e):
        x0, t0 = getattr(self, "_pan", None) or (e.x, self.t0)
        self.t0 = max(0.0, min(t0 - (e.x - x0) / self.pps, self._max_t0()))
        self._fitted = False
        self.redraw()
        return "break"

    def _pan_end(self, e):
        self._pan = None
        self.canvas.configure(cursor="")
        return "break"

    def _on_scrollbar(self, *args):
        view = self._view_w() / self.pps
        if args[0] == "moveto":
            self.t0 = float(args[1]) * self.duration
        elif args[0] == "scroll":
            n = int(args[1])
            self.t0 += n * view * (0.9 if args[2] == "pages" else 0.1)
        self.t0 = max(0.0, min(self.t0, self._max_t0()))
        self._fitted = False
        self.redraw()

    # ---------------------------------------------------------------- drawing

    def _lane_geom(self):
        h = max(self.canvas.winfo_height(), 60)
        n = max(len(self.lanes), 1)
        lane_h = max(min((h - RULER_H) / n, 120 * self.scale), 34)
        return lane_h

    LAYERS = 3                    # caption spots a line can sit in: 0 the usual one, 1 and 2 stacked above

    def _rows(self, lane_h: float):
        """(top of the block area, height of one spot's row) inside a lane. The block area holds
        three rows, the usual spot at the bottom; a block's row shows which spot its caption takes."""
        top = lane_h * 0.48
        return top, (lane_h - 4 - top) / self.LAYERS

    def _block_rows(self, b, y0: float, lane_h: float):
        """(y top, y bottom) of a block, by its caption spot (layer 0 at the bottom)."""
        top, row = self._rows(lane_h)
        layer = int(b.get("layer") or 0)
        base = y0 + lane_h - 4
        return base - row * (layer + 1) + 1, base - row * layer - 1

    def _colors(self) -> dict:
        p = self.palette()
        dark = self.dark()
        return {"bg": p["bg"], "fg": p["fg"], "muted": p["muted"], "band": p["band"], "line": p["hover"],
                "wave": "#4b6a8a" if dark else "#a9bdd2", "ruler": "#2a2a2a" if dark else "#f2f3f5",
                "gutter": "#252525" if dark else "#f7f7f8", "play": "#ff4d4f", "sel": "#4cc2ff" if dark else "#0067c0"}

    def redraw(self):
        c = self.canvas
        if not c.winfo_exists():
            return
        col = self._colors()
        c.delete("all")
        c.configure(background=col["bg"])
        W, H = c.winfo_width(), c.winfo_height()
        lane_h = self._lane_geom()
        t1 = self._t(W)
        # lanes
        for k, ln in enumerate(self.lanes):
            y0 = RULER_H + k * lane_h
            c.create_rectangle(GUTTER, y0, W, y0 + lane_h, fill=col["band"] if k % 2 else col["bg"], width=0)
            c.create_line(0, y0 + lane_h, W, y0 + lane_h, fill=col["line"])
            self._draw_wave(ln, y0 + 3, y0 + lane_h * 0.44, W, col)
            stacked = any(b.get("layer") for b in ln["blocks"])
            if stacked:                                  # faint lines between the caption spots
                top, row = self._rows(lane_h)
                for n in range(1, self.LAYERS):
                    yy = y0 + lane_h - 4 - row * n
                    c.create_line(GUTTER, yy, W, yy, fill=col["line"], dash=(1, 3))
            last_px = -1                                 # zoomed far out: one sliver per pixel is enough
            for b in ln["blocks"]:
                if b["end"] < self.t0 or b["start"] > t1:
                    continue
                px = int(self._x(b["end"]))
                if px <= last_px and (b["end"] - b["start"]) * self.pps < 1.5 and b["key"] != self.selected \
                        and not b.get("layer"):
                    continue
                last_px = max(last_px, px)
                if stacked or b.get("layer"):
                    by0, by1 = self._block_rows(b, y0, lane_h)
                else:                                    # nothing stacked in this lane: full height
                    by0, by1 = y0 + lane_h * 0.52, y0 + lane_h - 4
                self._draw_block(b, by0, by1, col, k, ln.get("one_voice"))
        # ruler and lane names over everything that scrolled under them
        c.create_rectangle(0, 0, W, RULER_H, fill=col["ruler"], width=0)
        self._draw_ruler(W, col)
        c.create_rectangle(0, RULER_H, GUTTER, H, fill=col["gutter"], width=0)
        c.create_line(GUTTER, 0, GUTTER, H, fill=col["line"])
        for k, ln in enumerate(self.lanes):
            y0 = RULER_H + k * lane_h
            c.create_text(10, y0 + lane_h / 2, text=self._fit_text(ln["name"] or "Audio", GUTTER - 16),
                          anchor="w", fill=col["fg"], font=self._font)
        if not self.lanes:
            c.create_text(GUTTER + 20, RULER_H + 30, anchor="w", fill=col["muted"], font=self._font,
                          text="Select a transcript to see its lines here.")
        self._draw_range()
        self._draw_playhead()
        # scrollbar
        view = self._view_w() / self.pps
        self.hbar.set(self.t0 / self.duration, min((self.t0 + view) / self.duration, 1.0))

    def _draw_ruler(self, W, col):
        c = self.canvas
        steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]
        step = next((s for s in steps if s * self.pps >= 70), steps[-1])
        minor = step / 5 if step * self.pps / 5 >= 8 else None
        t = (self.t0 // step) * step
        end = self._t(W)
        while t <= end:
            x = self._x(t)
            if x >= GUTTER:
                c.create_line(x, RULER_H - 8, x, RULER_H, fill=col["muted"])
                c.create_text(x + 3, 3, text=fmt(t, fine=step < 1), anchor="nw", fill=col["muted"], font=self._font)
            if minor:
                for i in range(1, 5):
                    xm = self._x(t + i * minor)
                    if GUTTER <= xm <= W:
                        c.create_line(xm, RULER_H - 4, xm, RULER_H, fill=col["line"])
            t += step

    def _draw_wave(self, ln, y0, y1, W, col):
        env = ln.get("env")
        if env is None or not len(env):
            return
        import numpy as np
        cols = int(W - GUTTER)
        if cols < 2:
            return
        if "norm" not in ln:
            top = float(np.percentile(env, 99.5))
            floor = min(float(np.percentile(env, 20)), top - 30.0)    # (steady sound still shows)
            ln["norm"] = (floor, max(top - floor, 6.0))
        floor, span = ln["norm"]
        t = self.t0 + np.arange(cols + 1) / self.pps - ln.get("env_offset", 0.0)
        idx = np.clip((t * ENV_RATE).astype(int), 0, len(env))
        vals = np.full(cols, floor, dtype=np.float32)
        last = len(env) - 1
        s, e = np.minimum(idx[:-1], last), np.minimum(idx[1:], last)
        inside = idx[:-1] < len(env)
        wide = inside & (e > s)                          # several values in this pixel: the loudest
        if wide.any():
            pairs = np.empty(2 * int(wide.sum()), dtype=np.int64)
            pairs[0::2], pairs[1::2] = s[wide], e[wide]
            vals[wide] = np.maximum.reduceat(env, pairs)[0::2]
        narrow = inside & ~wide                          # zoomed in: one value covers several pixels
        vals[narrow] = env[s[narrow]]
        amp = np.clip((vals - floor) / span, 0, 1) ** 1.3
        mid, half = (y0 + y1) / 2, (y1 - y0) / 2
        xs = GUTTER + np.arange(cols)
        top = [v for pair in zip(xs, mid - amp * half) for v in pair]
        bottom = [v for pair in zip(xs[::-1], (mid + amp * half)[::-1]) for v in pair]
        self.canvas.create_polygon(top + bottom, fill=col["wave"], outline="")

    def _fit_text(self, text: str, width: float) -> str:
        if width < 12:
            return ""
        key = (text, int(width))
        hit = self._fit_cache.get(key)
        if hit is not None:
            return hit
        if self._font.measure(text) <= width:
            out = text
        else:
            lo, hi = 0, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if self._font.measure(text[:mid] + "…") <= width:
                    lo = mid
                else:
                    hi = mid - 1
            out = (text[:lo] + "…") if lo else ""
        if len(self._fit_cache) > 4000:
            self._fit_cache.clear()
        self._fit_cache[key] = out
        return out

    def _draw_block(self, b, y0, y1, col, lane, one_voice=False):
        from transcriber.captions import contrast_text
        c = self.canvas
        x0, x1 = max(self._x(b["start"]), GUTTER), self._x(b["end"])
        if x1 <= GUTTER:
            return
        base = b.get("color") or "#64748b"
        sel = b["key"] == self.selected
        fill = base if sel else _mix(base, col["bg"], 0.35)
        shown = b.get("shown")                   # the caption stays on screen longer than the line (to be read)
        if shown and (y1 - y0) >= 8:
            faint = _mix(base, col["bg"], 0.7)
            if shown[1] > b["end"] + 0.05:
                tx0, tx1 = max(x1, GUTTER), self._x(shown[1])
                if tx1 > tx0:
                    c.create_rectangle(tx0, y0 + 2, tx1, y1 - 2, fill=faint, outline="", stipple="gray50")
                    c.create_line(tx1, y0 + 2, tx1, y1 - 2, fill=_mix(base, col["bg"], 0.4))
            if shown[0] < b["start"] - 0.05:
                hx0, hx1 = max(self._x(shown[0]), GUTTER), x0
                if hx1 > hx0:
                    c.create_rectangle(hx0, y0 + 2, hx1, y1 - 2, fill=faint, outline="", stipple="gray50")
        c.create_rectangle(x0, y0, max(x1, x0 + 2), y1, fill=fill, outline=col["sel"] if sel else _mix(base, "#000000", 0.25),
                           width=2 if sel else 1, tags=("block",))
        if b.get("edited"):
            c.create_oval(x0 + 3, y0 + 3, x0 + 8, y0 + 8, fill=contrast_text(fill), outline="")
        label = self._fit_text((f"{b['name']}: " if b.get("name") and not one_voice else "") + b["text"], x1 - x0 - 12)
        if label:
            c.create_text(x0 + (12 if b.get("edited") else 5), (y0 + y1) / 2, text=label, anchor="w",
                          fill=contrast_text(fill), font=self._font)
        if sel:                         # guides from the edges up through the waveform
            top = y0 - (y1 - y0) * 1.2
            for x in (x0, x1):
                c.create_line(x, top, x, y0, fill=col["sel"], dash=(2, 2))

    def _draw_playhead(self):
        c = self.canvas
        c.delete("playhead")
        if self.time is None:
            return
        x = self._x(self.time)
        if x < GUTTER or x > c.winfo_width():
            return
        col = self._colors()
        c.create_line(x, 0, x, c.winfo_height(), fill=col["play"], width=2, tags=("playhead",))
        c.create_polygon(x - 5, 0, x + 5, 0, x, 7, fill=col["play"], outline="", tags=("playhead",))

    # ---------------------------------------------------------------- finding things

    def _block(self, key):
        for k, ln in enumerate(self.lanes):
            for b in ln["blocks"]:
                if b["key"] == key:
                    return k, b
        return None

    def _hit(self, x, y):
        """(lane index, block, "left" | "right" | "move") under the mouse, or None."""
        if x < GUTTER or y < RULER_H:
            return None
        lane_h = self._lane_geom()
        k = int((y - RULER_H) // lane_h)
        if not 0 <= k < len(self.lanes):
            return None
        y_in = (y - RULER_H) - k * lane_h
        if y_in < lane_h * 0.44:
            return None                                       # over the waveform: that's for seeking
        best = None
        stacked = any(b.get("layer") for b in self.lanes[k]["blocks"])
        y0 = RULER_H + k * lane_h
        for b in self.lanes[k]["blocks"]:
            if stacked:                                           # only the block in the row under the mouse
                by0, by1 = self._block_rows(b, y0, lane_h)
                if not by0 - 2 <= y <= by1 + 2:
                    continue
            x0, x1 = self._x(b["start"]), self._x(b["end"])
            if x0 - HANDLE <= x <= x1 + HANDLE:
                if abs(x - x0) <= HANDLE and (x1 - x0 > 2 * HANDLE or x < (x0 + x1) / 2):
                    mode = "left"
                elif abs(x - x1) <= HANDLE:
                    mode = "right"
                elif x0 <= x <= x1:
                    mode = "move"
                else:
                    continue
                best = (k, b, mode)
                if mode != "move":
                    break
        return best

    def _neighbors(self, lane: int, b):
        """The nearest lines before and after b in its caption spot (row) that it is clear of. Lines
        in another spot don't hold it, nor does one it already sits on top of (a pasted copy stacked
        over the original, or two mics that caught the same moment): it can be dragged off it."""
        layer = int(b.get("layer") or 0)
        s0, e0 = b["start"], b["end"]
        prev = nxt = None
        for o in self.lanes[lane]["blocks"]:
            if o is b or int(o.get("layer") or 0) != layer:
                continue
            if o["end"] <= s0 + 0.002:
                if prev is None or o["end"] > prev["end"]:
                    prev = o
            elif o["start"] >= e0 - 0.002:
                if nxt is None or o["start"] < nxt["start"]:
                    nxt = o
        return prev, nxt

    # ---------------------------------------------------------------- mouse

    def _show_info(self, b, extra: str = ""):
        if b is None:
            self.info.configure(text="")
            return
        who = f"{b['name']}: " if b.get("name") else ""
        shown = b.get("shown")
        if shown and shown[1] > b["end"] + 0.05 and not extra:
            extra = f"   (caption stays until {fmt(shown[1])} so it can be read)"
        self.info.configure(text=f"{fmt(b['start'])} – {fmt(b['end'])}  ({b['end'] - b['start']:.1f} s)  "
                                 f"{who}{b['text'][:70]}{'…' if len(b['text']) > 70 else ''}{extra}")

    def _snip_point(self, b, x):
        """Where a click at x would split block b: (t, left, right) or None."""
        t = self._t(x)
        if self.snip_at:
            return self.snip_at(b["key"], t)
        words = b["text"].split()
        if len(words) < 2:
            return None
        n = min(max(round(len(words) * (t - b["start"]) / max(b["end"] - b["start"], 0.01)), 1), len(words) - 1)
        return t, " ".join(words[:n]), " ".join(words[n:])

    def _show_snip(self, e, hit):
        c = self.canvas
        c.delete("snipline")
        if not hit:
            c.configure(cursor="")
            self.info.configure(text="Snip: click a line where it should split")
            return
        k, b, _mode = hit
        res = self._snip_point(b, e.x)
        if not res:
            c.configure(cursor="X_cursor")
            self.info.configure(text="Nothing to split here (one word, or the very start or end)")
            return
        t, left, right = res
        lane_h = self._lane_geom()
        y0 = RULER_H + k * lane_h
        x = self._x(t)
        col = self._colors()
        c.create_line(x, y0 + 2, x, y0 + lane_h - 2, fill=col["play"], width=2, dash=(4, 3), tags=("snipline",))
        c.configure(cursor="crosshair")
        l = left if len(left) <= 28 else "\u2026" + left[-27:]
        r = right if len(right) <= 28 else right[:27] + "\u2026"
        self.info.configure(text=f"Snip at {fmt(t)}:  \u201c{l}\u201d  |  \u201c{r}\u201d")

    def _on_motion(self, e):
        if self._drag:
            return
        hit = self._hit(e.x, e.y)
        if self.tool == "snip":
            self._show_snip(e, hit)
            return
        cursor = ""
        if self.tool == "range" and e.x >= GUTTER:
            cursor = "sb_h_double_arrow"
            if hit:
                self._show_info(hit[1])
            else:
                self.info.configure(text="Range: drag over the part to play or export")
        elif hit:
            cursor = "sb_h_double_arrow" if hit[2] in ("left", "right") else "fleur"
            layer = int(hit[1].get("layer") or 0)
            self._show_info(hit[1], f"   (caption spot {layer + 1} of {self.LAYERS}, stacked)" if layer else "")
        elif e.x >= GUTTER:
            cursor = "hand2" if e.y < RULER_H else ""
            self._show_info(None)
        self.canvas.configure(cursor=cursor)

    def _on_press(self, e):
        self.canvas.focus_set()
        if e.x >= GUTTER and self.tool == "range":
            # mark a stretch: a drag sets it, a click (no drag) just jumps there
            self._drag = {"range": True, "x": e.x, "t": self._t(e.x), "moved": False}
            return
        hit = self._hit(e.x, e.y)
        if hit and self.tool == "snip":
            res = self._snip_point(hit[1], e.x)
            self.canvas.delete("snipline")
            if res and self.on_snip:
                self._drag = None
                self.on_snip(hit[1]["key"], res[0])
            else:
                self.bell()
            return
        if hit:
            k, b, mode = hit
            self.selected = b["key"]
            self._drag = {"lane": k, "b": b, "mode": mode, "s0": b["start"], "e0": b["end"], "x": e.x, "y": e.y,
                          "moved": False, "l0": int(b.get("layer") or 0)}
            if self.on_select:
                self.on_select(b["key"])
            self.redraw()
        elif e.x >= GUTTER:
            self._drag = {"seek": True}
            self._seek(self._t(e.x))

    def _seek(self, t, scrub: bool = False):
        """Jump the player there. While scrubbing (dragging the playhead) the player is told the
        latest spot a few times a second, not on every pixel the mouse moves."""
        t = max(0.0, min(t, self.duration))
        self.time = t
        self._draw_playhead()
        if not self.on_seek:
            return
        if not scrub:
            self._seek_pending = None
            if self._seek_job is not None:
                self.after_cancel(self._seek_job)
                self._seek_job = None
            self.on_seek(t)
            return
        self._seek_pending = t
        if self._seek_job is None:
            self._seek_job = self.after(40, self._seek_flush)

    def _seek_flush(self):
        self._seek_job = None
        t, self._seek_pending = self._seek_pending, None
        if t is not None and self.on_seek:
            self.on_seek(t, True)

    def _snap(self, t, kind, lane, b, alt):
        """t moved onto something nearby: the voice starting/stopping, a neighbour's edge, the playhead."""
        if alt or not self.snap_var.get():
            return t, None
        reach = SNAP_PX / self.pps
        ln = self.lanes[lane]
        cands = []
        on, off = ln.get("edges") or ([], [])
        cands += [(x, "voice") for x in (on if kind == "start" else off)]
        prev, nxt = self._neighbors(lane, b)
        if prev:
            cands.append((prev["end"], "line"))
        if nxt:
            cands.append((nxt["start"], "line"))
        if self.time is not None:
            cands.append((self.time, "playhead"))
        best = min(cands, key=lambda c: abs(c[0] - t), default=None)
        if best and abs(best[0] - t) <= reach:
            return best[0], best[1]
        return t, None

    def _on_drag(self, e):
        d = self._drag
        if not d:
            return
        if d.get("seek"):
            self._seek(self._t(e.x), scrub=True)
            return
        if d.get("range"):
            if abs(e.x - d["x"]) >= 3 or d["moved"]:
                d["moved"] = True
                self.set_range(d["t"], self._t(e.x), tell=False)
            return
        if abs(e.x - d["x"]) < 3 and abs(e.y - d["y"]) < 3 and not d["moved"]:
            return
        d["moved"] = True
        alt = alt_held(e)
        b, k = d["b"], d["lane"]
        if d["mode"] == "move" and self.on_layer:
            # up or down into another caption spot (the lane's rows), for people talking at once
            lane_h = self._lane_geom()
            top, row = self._rows(lane_h)
            base = RULER_H + k * lane_h + lane_h - 4
            layer = max(0, min(self.LAYERS - 1, int((base - e.y) // row)))
            if abs(e.y - d["y"]) > row * 0.6 or b.get("layer") != d["l0"]:
                b["layer"] = layer
            else:
                b["layer"] = d["l0"]
        dt = (e.x - d["x"]) / self.pps
        s, en = d["s0"], d["e0"]
        prev, nxt = self._neighbors(k, b)
        lo = min(prev["end"], d["s0"]) if prev else 0.0        # (lines that already overlap stay put)
        hi = max(nxt["start"], d["e0"]) if nxt else max(self.duration, d["e0"])
        what = None
        if d["mode"] == "move":
            length = en - s
            s = s + dt
            s2, w1 = self._snap(s, "start", k, b, alt)
            e2, w2 = self._snap(s + length, "end", k, b, alt)
            if w1 and (not w2 or abs(s2 - s) <= abs(e2 - (s + length))):
                s, what = s2, w1
            elif w2:
                s, what = e2 - length, w2
            s = min(max(s, lo), hi - length) if hi - lo >= length else lo
            en = s + length
        elif d["mode"] == "left":
            s, what = self._snap(s + dt, "start", k, b, alt)
            s = min(max(s, lo), en - MIN_LEN)
        else:
            en, what = self._snap(en + dt, "end", k, b, alt)
            en = max(min(en, hi), s + MIN_LEN)
        b["start"], b["end"] = s, en
        self.redraw()
        self._show_info(b, f"   (snapped to the {what})" if what else "")

    def _on_release(self, e):
        d, self._drag = self._drag, None
        if not d:
            return
        if d.get("seek"):
            self._seek(self._t(e.x))                # the final spot, right away
            return
        if d.get("range"):
            if d["moved"]:
                self.set_range(d["t"], self._t(e.x))
            else:
                self._seek(d["t"])                  # a click on the ruler: just jump there
            return
        b = d["b"]
        if not d["moved"]:
            self._seek(b["start"])                  # a click: play from this line
            return
        if abs(b["start"] - d["s0"]) > 0.004 or abs(b["end"] - d["e0"]) > 0.004:
            if self.on_change:
                self.on_change(b["key"], b["start"], b["end"])
        if int(b.get("layer") or 0) != d.get("l0", 0) and self.on_layer:
            self.on_layer(b["key"], int(b.get("layer") or 0))

    def _cancel_drag(self):
        d, self._drag = self._drag, None
        if d and d.get("range"):
            if d["moved"]:
                self.set_range(None, tell=False)
            return
        if d and not d.get("seek"):
            d["b"]["start"], d["b"]["end"] = d["s0"], d["e0"]
            d["b"]["layer"] = d.get("l0", 0)
            self.redraw()

    def _on_double(self, e):
        """Double-click a line: edit its text. Anywhere else (the ruler, a gap, the Snip or Range
        tool) a quick second click is just another press: Tk sends it here instead of _on_press."""
        hit = self._hit(e.x, e.y)
        if self.tool in ("snip", "range") or not hit or not self.on_edit_text:
            return self._on_press(e)
        self._drag = None
        self.on_edit_text(hit[1]["key"])

    def _on_right_click(self, e):
        """Right-click a line: select it, and the app shows its menu (the transcript row's)."""
        hit = self._hit(e.x, e.y)
        if not self.on_menu:
            return "break"
        in_range = bool(self.range and e.x >= GUTTER and self.range[0] <= self._t(e.x) <= self.range[1])
        self.canvas.focus_set()
        self._drag = None
        if not hit:
            if in_range:                             # the marked stretch itself: play it, export it
                self.on_menu(None, e.x_root, e.y_root, True)
            return "break"
        self.selected = hit[1]["key"]
        if self.on_select:
            self.on_select(self.selected)
        self.redraw()
        self.on_menu(hit[1]["key"], e.x_root, e.y_root, in_range)
        return "break"

    def _delete_selected(self, _e=None):
        if self.selected is not None and self.on_delete and self._drag is None:
            key, self.selected = self.selected, None
            self.on_delete(key)
        return "break"

    def _nudge(self, dt):
        if self.selected is None:
            return "break"
        found = self._block(self.selected)
        if not found:
            return "break"
        k, b = found
        prev, nxt = self._neighbors(k, b)
        length = b["end"] - b["start"]
        lo = prev["end"] if prev else 0.0
        hi = (nxt["start"] if nxt else self.duration) - length
        s = min(max(b["start"] + dt, lo), max(hi, lo))
        if abs(s - b["start"]) > 0.001 and self.on_change:
            self.on_change(b["key"], s, s + length)
        return "break"
