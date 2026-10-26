"""People: names, photos and voice samples used to recognize who is speaking.

Stored in the app's settings folder (%APPDATA%\\WhisperTranscriber):
    people.json        names, colors, photo file names, voiceprints
    avatars\\<id>.png   each photo, cropped to a circle (a private copy, so moving the original is fine)

A voiceprint is the list of numbers the speaker model produces for a voice. Comparing them
(cosine similarity) tells how alike two voices are.
"""

from __future__ import annotations

import json
import math
import os
import uuid
from pathlib import Path
from typing import Optional

import sys

if sys.platform == "darwin":
    APP_DIR = Path.home() / "Library" / "Application Support" / "WhisperTranscriber"
else:
    APP_DIR = Path(os.environ.get("APPDATA", Path.home() / ".config")) / "WhisperTranscriber"
PEOPLE_FILE = APP_DIR / "people.json"
AVATAR_DIR = APP_DIR / "avatars"

# Colors for names and for initials circles (readable on both light and dark backgrounds).
PERSON_COLORS = ["#3b82f6", "#f97316", "#10b981", "#a855f7", "#ef4444", "#14b8a6", "#eab308", "#ec4899"]
UNKNOWN_COLORS = ["#64748b", "#78716c", "#6b7280", "#71717a"]

MATCH_THRESHOLD = 0.55     # how alike a voice must be to count as a known person (0..1)
MAX_VOICES = 24            # voiceprints kept per person (newest win)


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class People:
    def __init__(self, path: Path = PEOPLE_FILE):
        self.path = Path(path)
        self.people: list[dict] = []
        self.load()

    # ------------------------------------------------------------ storage

    def load(self):
        try:
            self.people = json.loads(self.path.read_text(encoding="utf-8")).get("people", [])
        except (OSError, ValueError):
            self.people = []

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "people": self.people}), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    # ------------------------------------------------------------ people

    def get(self, pid: Optional[str]) -> Optional[dict]:
        return next((p for p in self.people if p["id"] == pid), None) if pid else None

    def by_name(self, name: str) -> Optional[dict]:
        return next((p for p in self.people if p["name"].lower() == name.strip().lower()), None)

    def add(self, name: str) -> dict:
        used = {p["color"] for p in self.people}
        color = next((c for c in PERSON_COLORS if c not in used), PERSON_COLORS[len(self.people) % len(PERSON_COLORS)])
        person = {"id": uuid.uuid4().hex[:12], "name": name.strip() or "Someone", "color": color,
                  "photo": "", "voices": []}
        self.people.append(person)
        self.save()
        return person

    def rename(self, pid: str, name: str):
        p = self.get(pid)
        if p and name.strip():
            p["name"] = name.strip()
            self.save()

    def delete(self, pid: str):
        p = self.get(pid)
        if not p:
            return
        self._remove_files(p)
        self.people.remove(p)
        self.save()

    @staticmethod
    def _remove_files(p: dict):
        for key in ("photo", "photo_original"):
            if p.get(key):
                try:
                    (AVATAR_DIR / p[key]).unlink()
                except OSError:
                    pass

    def set_photo(self, pid: str, image_path: str, crop: Optional[tuple] = None) -> bool:
        """Save a photo for this person.

        crop = (left, top, side) in the picture's own pixels (after phone rotation is applied);
        None means a centered square. A copy of the original (up to 1600 px) is kept so the crop
        can be adjusted later, and the cropped square is saved at 256 px. The shape (circle,
        rounded, square) is applied when shown, so changing that setting needs no re-cropping.
        """
        p = self.get(pid)
        if not p:
            return False
        try:
            from PIL import Image, ImageOps
            img = ImageOps.exif_transpose(Image.open(image_path)).convert("RGBA")   # respect phone rotation
            scale = min(1.0, 1600 / max(img.size))
            if scale < 1:
                img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
                if crop:
                    crop = tuple(v * scale for v in crop)
            AVATAR_DIR.mkdir(parents=True, exist_ok=True)
            token = uuid.uuid4().hex[:6]                       # new names, so cached images refresh
            original = f"{pid}-{token}-original.png"
            img.save(AVATAR_DIR / original)
        except Exception:
            return False
        old = {k: p.get(k) for k in ("photo", "photo_original")}
        p["photo_original"] = original
        if not self.recrop(pid, crop):
            p.update(old)
            return False
        self._remove_files(old)
        return True

    def recrop(self, pid: str, crop: Optional[tuple] = None) -> bool:
        """Cut the person's photo out of their saved original again (used by "Adjust crop")."""
        p = self.get(pid)
        if not p or not p.get("photo_original"):
            return False
        try:
            from PIL import Image
            img = Image.open(AVATAR_DIR / p["photo_original"]).convert("RGBA")
            left, top, side = default_crop(img.size) if crop is None else crop
            side = max(1, min(side, img.width, img.height))
            left = min(max(0, left), img.width - side)
            top = min(max(0, top), img.height - side)
            square = img.crop((round(left), round(top), round(left + side), round(top + side)))
            square = square.resize((256, 256), Image.LANCZOS)
            name = f"{pid}-{uuid.uuid4().hex[:6]}.png"
            square.save(AVATAR_DIR / name)
        except Exception:
            return False
        old = p.get("photo")
        p["photo"], p["crop"] = name, [round(left, 1), round(top, 1), round(side, 1)]
        self.save()
        if old and old != name:
            try:
                (AVATAR_DIR / old).unlink()
            except OSError:
                pass
        return True

    def original_path(self, pid: str) -> Optional[Path]:
        p = self.get(pid)
        if p and p.get("photo_original") and (AVATAR_DIR / p["photo_original"]).exists():
            return AVATAR_DIR / p["photo_original"]
        return None

    def remove_photo(self, pid: str):
        p = self.get(pid)
        if p and (p.get("photo") or p.get("photo_original")):
            self._remove_files(p)
            p["photo"] = p["photo_original"] = ""
            p.pop("crop", None)
            self.save()

    # ------------------------------------------------------------ voices

    def add_voice(self, pid: str, voiceprint) -> None:
        p = self.get(pid)
        if p and voiceprint:
            p["voices"] = (p.get("voices", []) + [[round(float(x), 5) for x in voiceprint]])[-MAX_VOICES:]
            self.save()

    def match(self, voiceprint, exclude: set[str] = frozenset()) -> tuple[Optional[dict], float]:
        """The known person whose voice is most like this one, if alike enough."""
        best, best_score = None, 0.0
        if not voiceprint:
            return None, 0.0
        for p in self.people:
            if p["id"] in exclude or not p.get("voices"):
                continue
            score = max(cosine(voiceprint, v) for v in p["voices"])
            if score > best_score:
                best, best_score = p, score
        return (best, best_score) if best_score >= MATCH_THRESHOLD else (None, best_score)


# ---------------------------------------------------------------- avatar images

def default_crop(size) -> tuple:
    """A centered square, nudged up a little because faces usually sit in the upper part."""
    w, h = size
    side = min(w, h)
    return ((w - side) / 2, (h - side) / 3, side)


SHAPES = {"circle": "Circle", "rounded": "Rounded square", "square": "Square"}


def _shape_mask(size: int, shape: str = "circle"):
    """A smooth-edged mask for the avatar shape (drawn 4x larger, then shrunk)."""
    from PIL import Image, ImageDraw
    big = size * 4
    mask = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(mask)
    if shape == "square":
        draw.rectangle((0, 0, big - 1, big - 1), fill=255)
    elif shape == "rounded":
        draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=int(big * 0.24), fill=255)
    else:
        draw.ellipse((0, 0, big - 1, big - 1), fill=255)
    return mask.resize((size, size), Image.LANCZOS)


def _font(size: int):
    from PIL import ImageFont
    for name in ("segoeuib.ttf", "seguisb.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def avatar_pil(person: Optional[dict], size: int, label: str = "", color: str = "#64748b",
               shape: str = "circle"):
    """RGBA avatar in the chosen shape: the person's photo, or their initials on their color."""
    from PIL import Image, ImageDraw
    if person and person.get("photo"):
        try:
            img = Image.open(AVATAR_DIR / person["photo"]).convert("RGBA").resize((size, size), Image.LANCZOS)
            img.putalpha(_shape_mask(size, shape))
            return img
        except OSError:
            pass
    name = person["name"] if person else label
    fill = person["color"] if person else color
    big = size * 4
    img = Image.new("RGBA", (big, big), fill)
    draw = ImageDraw.Draw(img)
    initial = _initials(name)
    font = _font(int(big * (0.44 if len(initial) == 1 else 0.36)))
    box = draw.textbbox((0, 0), initial, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    draw.text(((big - w) / 2 - box[0], (big - h) / 2 - box[1]), initial, fill="white", font=font)
    img = img.resize((size, size), Image.LANCZOS)
    img.putalpha(_shape_mask(size, shape))
    return img


def _initials(name: str) -> str:
    name = name.strip()
    if name.lower().startswith("speaker "):
        return name.split()[-1][:2]                       # "Speaker 3" -> "3"
    parts = [w for w in name.replace("-", " ").split() if w]
    if not parts:
        return "?"
    return parts[0][0].upper() if len(parts) == 1 else (parts[0][0] + parts[-1][0]).upper()


class AvatarCache:
    """Tk images for avatars, made once per person, size and shape (Tk drops images nobody holds)."""

    def __init__(self):
        self._images: dict = {}
        self.shape = "circle"

    def get(self, person: Optional[dict], size: int, label: str = "", color: str = "#64748b"):
        from PIL import ImageTk
        base = (person["id"], person.get("photo"), person["name"], person["color"]) if person else (label, color)
        key = base + (size, self.shape)
        if key not in self._images:
            self._images[key] = ImageTk.PhotoImage(avatar_pil(person, size, label, color, self.shape))
        return self._images[key]

    def clear(self):
        self._images.clear()
