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
import time
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
VOICE_DIR = APP_DIR / "voices"          # a short clip of each voice sample, to listen to

# Colors for names and for initials circles (readable on both light and dark backgrounds).
PERSON_COLORS = ["#3b82f6", "#f97316", "#10b981", "#a855f7", "#ef4444", "#14b8a6", "#eab308", "#ec4899"]
UNKNOWN_COLORS = ["#64748b", "#78716c", "#6b7280", "#71717a"]

MATCH_THRESHOLD = 0.55     # how alike a voice must be to count as a known person (0..1)
MAX_VOICES = 24            # voiceprints kept per person (newest win)
ODD_ONE_OUT = 0.4          # a sample this unlike the person's others is probably not them (or silence)


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    score = dot / (na * nb) if na and nb else 0.0
    return score if math.isfinite(score) else 0.0          # a broken voiceprint matches nothing


def _remove_clip(name: Optional[str]) -> None:
    if name:
        try:
            (VOICE_DIR / name).unlink()
        except OSError:
            pass


def new_clip_path() -> Path:
    """Where to keep a new voice sample's clip."""
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    return VOICE_DIR / f"{uuid.uuid4().hex[:12]}.wav"


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
        if self._tidy_voices():
            self.save()

    def _tidy_voices(self) -> bool:
        """Drop voice samples that can't be right: broken ones (silence can give those), exact
        copies, and a sample saved under two people (a speaker named, then renamed) stays only with
        the one it sounds like. Returns True if anything changed."""
        changed = False
        for p in self.people:
            infos, seen, keep = self._infos(p), set(), []
            for k, v in enumerate(p.get("voices", [])):
                key = tuple(v) if self.usable_print(v) else None
                if key is None or key in seen:
                    _remove_clip(infos[k].get("audio"))
                    continue
                seen.add(key)
                keep.append(k)
            if len(keep) != len(p.get("voices", [])):
                p["voices"] = [p["voices"][k] for k in keep]
                p["voice_info"] = [infos[k] for k in keep]
                changed = True
        owners: dict = {}
        for p in self.people:
            for v in p.get("voices", []):
                owners.setdefault(tuple(v), []).append(p)
        for key, ps in owners.items():
            if len(ps) < 2:
                continue
            def fit(p, key=key):
                others = [w for w in p["voices"] if tuple(w) != key]
                return max((cosine(key, w) for w in others), default=-1.0)
            best = max(ps, key=fit)
            if fit(best) < 0:                    # nothing else to go by: leave it
                continue
            for p in ps:
                if p is not best:
                    k = next(i for i, w in enumerate(p["voices"]) if tuple(w) == key)
                    infos = self._infos(p)
                    p["voices"].pop(k)
                    _remove_clip(infos.pop(k).get("audio"))
                    changed = True
        return changed

    def forget_voice(self, voiceprint, keep_pid: Optional[str] = None) -> None:
        """Remove this exact voice sample from everyone but keep_pid (it was learned under the wrong name)."""
        key = [round(float(x), 5) for x in voiceprint or []]
        changed = False
        for p in self.people:
            if p["id"] == keep_pid:
                continue
            infos = self._infos(p)
            for k in range(len(p.get("voices", [])) - 1, -1, -1):
                if p["voices"][k] == key:
                    p["voices"].pop(k)
                    _remove_clip(infos.pop(k).get("audio"))
                    changed = True
        if changed:
            self.save()

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

    def set_color(self, pid: str, color: str):
        p = self.get(pid)
        if p and color and color.startswith("#") and len(color) == 7:
            p["color"] = color.lower()
            self.save()

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
        for info in p.get("voice_info") or []:
            _remove_clip(info.get("audio"))
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

    @staticmethod
    def usable_print(voiceprint) -> bool:
        """A real voiceprint (not empty, all zeros or broken numbers, which silence can give)."""
        try:
            vals = [float(x) for x in voiceprint or []]
        except (TypeError, ValueError):
            return False
        return bool(vals) and all(math.isfinite(x) for x in vals) and sum(x * x for x in vals) > 1e-6

    @staticmethod
    def _infos(p: dict) -> list:
        """One info dict per voice sample (older samples get a blank one)."""
        voices = p.get("voices", [])
        infos = list(p.get("voice_info") or [])[:len(voices)]
        infos += [{} for _ in range(len(voices) - len(infos))]
        p["voice_info"] = infos
        return infos

    def voice_samples(self, pid: str) -> list:
        """[(voiceprint, info)] oldest first. info: "added", "from", "audio" (clip file name), "seconds"."""
        p = self.get(pid)
        return list(zip(p.get("voices", []), self._infos(p))) if p else []

    def add_voice(self, pid: str, voiceprint, info: Optional[dict] = None) -> bool:
        """Learn a voice sample. Returns False (and keeps nothing) if the voiceprint isn't usable."""
        p = self.get(pid)
        if not p or not self.usable_print(voiceprint):
            if info and info.get("audio"):
                _remove_clip(info["audio"])
            return False
        infos = self._infos(p)
        vp = [round(float(x), 5) for x in voiceprint]
        if vp in p.get("voices", []):                     # already learned (the same speaker named again)
            if info and info.get("audio"):
                _remove_clip(info["audio"])
            return True
        p["voices"] = p.get("voices", []) + [vp]
        infos.append(dict(info or {}, added=(info or {}).get("added") or time.strftime("%Y-%m-%d %H:%M")))
        while len(p["voices"]) > MAX_VOICES:              # the oldest go first
            p["voices"].pop(0)
            _remove_clip(infos.pop(0).get("audio"))
        self.save()
        return True

    def remove_voice(self, pid: str, index: int) -> None:
        p = self.get(pid)
        if not p or not 0 <= index < len(p.get("voices", [])):
            return
        infos = self._infos(p)
        p["voices"].pop(index)
        _remove_clip(infos.pop(index).get("audio"))
        self.save()

    def voice_fit(self, pid: str) -> list:
        """How much each sample sounds like the person's other samples (0..1; None for a single sample)."""
        voices = (self.get(pid) or {}).get("voices", [])
        if len(voices) == 2:                       # can't tell which one is off, only that they differ
            return [cosine(voices[0], voices[1])] * 2
        if len(voices) < 2:
            return [None] * len(voices)
        out = []
        for i, v in enumerate(voices):
            scores = sorted(cosine(v, w) for j, w in enumerate(voices) if j != i)
            out.append(scores[len(scores) // 2])
        return out

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
