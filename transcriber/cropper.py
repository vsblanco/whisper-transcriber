"""Crop window for profile pictures: drag to position, zoom with the slider or mouse wheel.

The part outside the chosen shape (circle, rounded square or square) is dimmed, so you see
exactly what the picture will look like.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional


class CropDialog(tk.Toplevel):
    """CropDialog(parent, image_path, shape, on_done) calls on_done((left, top, side)) in the
    picture's own pixels (phone rotation applied), or nothing if cancelled."""

    def __init__(self, parent, image_path: str, shape: str, on_done: Callable[[tuple], None],
                 start: Optional[tuple] = None, ui_scale: float = 1.0, dark: bool = False):
        super().__init__(parent)
        from PIL import Image, ImageOps
        self.title("Crop photo")
        self.transient(parent)
        self.resizable(False, False)
        self.on_done = on_done
        self.shape = shape
        self.dark = dark

        img = ImageOps.exif_transpose(Image.open(image_path)).convert("RGBA")
        self.full_size = img.size
        # Work on a smaller copy for smooth dragging; results are mapped back to full size.
        self.work_scale = min(1.0, 1400 / max(img.size))
        self.work = img if self.work_scale == 1 else img.resize(
            (round(img.width * self.work_scale), round(img.height * self.work_scale)), Image.LANCZOS)

        self.view = int(360 * ui_scale)          # canvas size
        self.frame_px = int(self.view * 0.72)    # crop area size
        self.f0 = (self.view - self.frame_px) // 2
        w, h = self.work.size
        self.min_scale = self.frame_px / min(w, h)   # zoom 1 = picture just covers the crop area

        frame = ttk.Frame(self, padding=(18, 14))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Drag to position. Zoom with the slider or your mouse wheel.",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        bg = "#1c1c1c" if dark else "#f3f3f3"
        self.canvas = tk.Canvas(frame, width=self.view, height=self.view, highlightthickness=0,
                                background=bg, cursor="fleur")
        self.canvas.pack()
        self._overlay = self._make_overlay(bg)

        zoom_row = ttk.Frame(frame)
        zoom_row.pack(fill="x", pady=(10, 0))
        ttk.Label(zoom_row, text="Zoom").pack(side="left")
        self.zoom = tk.DoubleVar(value=1.0)
        ttk.Scale(zoom_row, from_=1.0, to=5.0, variable=self.zoom, orient="horizontal",
                  command=lambda _v: self._zoom_changed()).pack(side="left", fill="x", expand=True, padx=10)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text="Reset", command=self._reset).pack(side="left")
        ttk.Button(buttons, text="Done", style="Accent.TButton", command=self._done).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(0, 8))

        # Position: the picture's top-left corner on the canvas, in canvas pixels.
        self.ox = self.oy = 0.0
        self._last_zoom = 1.0
        if start:
            self._apply_start(start)
        else:
            self._reset(draw=False)

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<MouseWheel>", self._wheel)                      # Windows / macOS
        self.canvas.bind("<Button-4>", lambda e: self._wheel(e, 120))      # Linux
        self.canvas.bind("<Button-5>", lambda e: self._wheel(e, -120))
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: self._done())
        self._draw()

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{x}+{y}")
        self.grab_set()

    # ---------------------------------------------------------- geometry

    @property
    def scale(self) -> float:
        return self.min_scale * self.zoom.get()

    def _clamp(self):
        """Keep the crop area covered by the picture."""
        w, h = self.work.size
        dw, dh = w * self.scale, h * self.scale
        f0, f1 = self.f0, self.f0 + self.frame_px
        self.ox = min(f0, max(f1 - dw, self.ox))
        self.oy = min(f0, max(f1 - dh, self.oy))

    def _reset(self, draw: bool = True):
        from transcriber.people import default_crop
        left, top, side = default_crop(self.work.size)
        self.zoom.set(1.0)
        self._last_zoom = 1.0
        self.ox, self.oy = self.f0 - left * self.scale, self.f0 - top * self.scale
        self._clamp()
        if draw:
            self._draw()

    def _apply_start(self, start):
        left, top, side = (v * self.work_scale for v in start)
        zoom = max(1.0, min(5.0, self.frame_px / side / self.min_scale))
        self.zoom.set(zoom)
        self._last_zoom = zoom
        self.ox, self.oy = self.f0 - left * self.scale, self.f0 - top * self.scale
        self._clamp()

    def _zoom_changed(self, anchor=None):
        """Zoom around the crop area's center (or the mouse), keeping that point still."""
        ax, ay = anchor or (self.view / 2, self.view / 2)
        old = self.min_scale * self._last_zoom
        new = self.scale
        self.ox = ax - (ax - self.ox) * new / old
        self.oy = ay - (ay - self.oy) * new / old
        self._last_zoom = self.zoom.get()
        self._clamp()
        self._draw()

    def _press(self, e):
        self._grab = (e.x, e.y, self.ox, self.oy)

    def _drag(self, e):
        x0, y0, ox, oy = self._grab
        self.ox, self.oy = ox + e.x - x0, oy + e.y - y0
        self._clamp()
        self._draw()

    def _wheel(self, e, delta=None):
        step = (delta if delta is not None else e.delta) / 120 * 0.15
        self.zoom.set(max(1.0, min(5.0, self.zoom.get() * (1 + step))))
        self._zoom_changed(anchor=(e.x, e.y))

    # ---------------------------------------------------------- drawing

    def _make_overlay(self, bg):
        """Dim everything except the crop shape, and outline it."""
        from PIL import Image, ImageDraw, ImageTk
        from transcriber.people import _shape_mask
        v, f = self.view, self.frame_px
        dim = (0, 0, 0, 150) if self.dark else (255, 255, 255, 170)
        overlay = Image.new("RGBA", (v, v), dim)
        hole = _shape_mask(f, self.shape)
        clear = Image.new("RGBA", (f, f), (0, 0, 0, 0))
        region = overlay.crop((self.f0, self.f0, self.f0 + f, self.f0 + f))
        region.paste(clear, (0, 0), hole)
        overlay.paste(region, (self.f0, self.f0))
        line = Image.new("RGBA", (v * 2, v * 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(line)
        box = (self.f0 * 2, self.f0 * 2, (self.f0 + f) * 2 - 1, (self.f0 + f) * 2 - 1)
        outline = (255, 255, 255, 230) if self.dark else (60, 60, 60, 200)
        if self.shape == "square":
            d.rectangle(box, outline=outline, width=3)
        elif self.shape == "rounded":
            d.rounded_rectangle(box, radius=int(f * 2 * 0.24), outline=outline, width=3)
        else:
            d.ellipse(box, outline=outline, width=3)
        overlay.alpha_composite(line.resize((v, v), Image.LANCZOS))
        return ImageTk.PhotoImage(overlay)

    def _draw(self):
        from PIL import Image, ImageTk
        w, h = self.work.size
        s = self.scale
        # Only resize the part of the picture that's visible, so big zooms stay fast.
        x0, y0 = max(0, -self.ox / s), max(0, -self.oy / s)
        x1, y1 = min(w, (self.view - self.ox) / s), min(h, (self.view - self.oy) / s)
        if x1 <= x0 or y1 <= y0:
            return
        part = self.work.crop((int(x0), int(y0), int(x1) + 1, int(y1) + 1))
        size = (max(1, round(part.width * s)), max(1, round(part.height * s)))
        self._shown = ImageTk.PhotoImage(part.resize(size, Image.BILINEAR))
        self.canvas.delete("all")
        self.canvas.create_image(self.ox + int(x0) * s, self.oy + int(y0) * s, image=self._shown, anchor="nw")
        self.canvas.create_image(0, 0, image=self._overlay, anchor="nw")

    def crop_box(self) -> tuple:
        """The crop area in the full-size picture's pixels: (left, top, side)."""
        s = self.scale * self.work_scale       # canvas px per full-size px
        return ((self.f0 - self.ox) / s, (self.f0 - self.oy) / s, self.frame_px / s)

    def _done(self):
        box = self.crop_box()
        self.destroy()
        self.on_done(box)
