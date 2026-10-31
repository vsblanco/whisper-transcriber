"""Captions straight into a running DaVinci Resolve, onto the timeline that's open.

Resolve ships a Python module (DaVinciResolveScript) that talks to the running app once
"External scripting using: Local" is on (Preferences > System > General). Two ways in:

- a subtitle track: the captions as an .srt, imported into the media pool and put on the timeline
  (Resolve's own subtitles, styled per track in the Inspector);
- Text+ titles: one title clip per caption on a new video track, each in its person's color, made
  from a Text+ the user has in the media pool (drag Text+ from Effects > Titles into the media pool
  once, style it how the captions should look). Captions stacked above another person's go on a
  second track.

Nothing here imports Resolve's module at load time: connect() finds it when asked."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Optional


class ResolveError(Exception):
    """A plain-words reason it couldn't be done (shown to the user as it is)."""


def module_dirs() -> list:
    """Where Resolve installs its scripting module, per platform (plus RESOLVE_SCRIPT_API)."""
    out = []
    env = os.environ.get("RESOLVE_SCRIPT_API")
    if env:
        out.append(str(Path(env) / "Modules"))
    if sys.platform == "win32":
        base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        out.append(str(Path(base) / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "Developer" / "Scripting" / "Modules"))
    elif sys.platform == "darwin":
        out.append("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules")
    else:
        out.append("/opt/resolve/Developer/Scripting/Modules")
        out.append("/home/resolve/Developer/Scripting/Modules")
    return out


def connect(loader: Optional[Callable] = None):
    """The Resolve object, or a ResolveError saying what to do. loader: a stand-in for
    importing DaVinciResolveScript (tests)."""
    if loader is None:
        def loader():
            try:
                import DaVinciResolveScript as dvr       # noqa: N813
                return dvr
            except ImportError:
                pass
            for d in module_dirs():
                if os.path.isdir(d) and d not in sys.path:
                    sys.path.append(d)
            try:
                import DaVinciResolveScript as dvr       # noqa: N813
                return dvr
            except ImportError:
                return None
    dvr = loader()
    if dvr is None:
        raise ResolveError("DaVinci Resolve's scripting module wasn't found. Is Resolve installed? (It lives in "
                           "Resolve's Support\\Developer\\Scripting\\Modules folder.)")
    try:
        resolve = dvr.scriptapp("Resolve")
    except Exception as exc:
        raise ResolveError(f"Couldn't talk to DaVinci Resolve: {exc}")
    if not resolve:
        raise ResolveError("DaVinci Resolve isn't open, or external scripting is off. Open Resolve, then in "
                           "Preferences > System > General set \u201cExternal scripting using\u201d to Local.")
    return resolve


def open_timeline(resolve) -> dict:
    """{"project", "timeline", "pool", "name", "fps", "start", "end"} for the timeline that's open."""
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject() if pm else None
    if not project:
        raise ResolveError("No project is open in DaVinci Resolve.")
    timeline = project.GetCurrentTimeline()
    if not timeline:
        raise ResolveError("No timeline is open in DaVinci Resolve. Open the one the captions go on (the Edit page).")
    fps = 0.0
    for getter in (lambda: timeline.GetSetting("timelineFrameRate"), lambda: project.GetSetting("timelineFrameRate")):
        try:
            fps = float(getter() or 0)
        except Exception:
            fps = 0.0
        if fps:
            break
    if not fps:
        fps = 24.0
    start = int(timeline.GetStartFrame() or 0)
    end = int(timeline.GetEndFrame() or 0)
    return {"project": project, "timeline": timeline, "pool": project.GetMediaPool(), "name": timeline.GetName(),
            "fps": fps, "start": start, "end": end}


# ---------------------------------------------------------------- a subtitle track

def send_subtitle_track(info: dict, srt_path: str) -> int:
    """The .srt into the media pool and onto the timeline as a subtitle track. Returns how many
    items landed (1: the track)."""
    pool, timeline = info["pool"], info["timeline"]
    items = pool.ImportMedia([str(srt_path)])
    if not items:
        raise ResolveError("Resolve didn't take the captions file (media pool import returned nothing).")
    try:
        timeline.AddTrack("subtitle")
    except Exception:
        pass
    placed = pool.AppendToTimeline(list(items))
    if not placed:
        raise ResolveError("Resolve imported the captions but didn't put them on the timeline. Drag the .srt "
                           "from the media pool onto the timeline instead (it's in the media pool now).")
    return len(placed)


# ---------------------------------------------------------------- Text+ titles

def find_text_template(pool):
    """A Text+ (or any Fusion title) in the media pool to make the captions from, or None."""
    def walk(folder):
        for clip in folder.GetClipList() or []:
            try:
                props = clip.GetClipProperty() or {}
            except Exception:
                props = {}
            name = str(props.get("Clip Name") or "")
            kind = str(props.get("Type") or "")
            if "Text+" in name or "Fusion Title" in kind or "Generator" in kind and "Text" in name:
                return clip
        for sub in folder.GetSubFolderList() or []:
            found = walk(sub)
            if found is not None:
                return found
        return None
    root = pool.GetRootFolder()
    return walk(root) if root else None


def clip_list(cues: list, template, info: dict, track: int, track_above: int) -> tuple:
    """The clipInfo dicts for AppendToTimeline, one per caption, on whole frames inside the
    timeline; and which cue each one is for. Captions stacked above another go on track_above."""
    fps, start, end = info["fps"], info["start"], info["end"]
    out, which = [], []
    last_end: dict = {}
    for k, c in enumerate(sorted(cues, key=lambda c: (c.start, c.end))):
        a = int(round(c.start * fps))
        b = int(round(c.end * fps))
        if b <= a:
            b = a + 1
        t = track_above if c.layer else track
        if a < last_end.get(t, -1):                    # two on the same frames: nudge the later one
            a = last_end[t]
            if b <= a:
                continue
        rec = start + a
        if end and rec >= end:
            continue
        out.append({"mediaPoolItem": template, "startFrame": 0, "endFrame": b - a - 1,
                    "recordFrame": rec, "trackIndex": t, "mediaType": 1})
        which.append(c)
        last_end[t] = b
    return out, which


def _rgb(color: str) -> tuple:
    c = (color or "#ffffff").lstrip("#")
    try:
        return tuple(int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return (1.0, 1.0, 1.0)


def style_item(item, text: str, color: Optional[str] = None, size: Optional[float] = None) -> bool:
    """Put the caption's words (and color) into a placed Text+ clip. False when the clip has no
    Text+ inside (not a Text+ template)."""
    try:
        if not item.GetFusionCompCount():
            return False
        comp = item.GetFusionCompByIndex(1)
        tool = comp.FindToolByID("TextPlus") if comp else None
        if tool is None:
            return False
        tool.SetInput("StyledText", text)
        if color:
            r, g, b = _rgb(color)
            tool.SetInput("Red1", r)
            tool.SetInput("Green1", g)
            tool.SetInput("Blue1", b)
        if size:
            tool.SetInput("Size", float(size))
        return True
    except Exception:
        return False


def send_text_plus(info: dict, cues: list, colors: bool = True, size: Optional[float] = None,
                   progress: Optional[Callable[[str], None]] = None) -> dict:
    """One Text+ per caption on a new video track (two when captions stack). Returns
    {"placed", "styled", "track"}."""
    pool, timeline = info["pool"], info["timeline"]
    template = find_text_template(pool)
    if template is None:
        raise ResolveError("No Text+ in the media pool to make the captions from. In Resolve, drag Text+ from "
                           "Effects > Titles into the media pool (style it the way the captions should look), "
                           "then send again.")
    if not cues:
        raise ResolveError("No captions to send.")
    track = int(timeline.GetTrackCount("video") or 0) + 1
    if not timeline.AddTrack("video"):
        raise ResolveError("Resolve wouldn't add a video track for the captions.")
    track_above = track
    if any(c.layer for c in cues):
        if timeline.AddTrack("video"):
            track_above = track + 1
    infos, which = clip_list(cues, template, info, track, track_above)
    if progress:
        progress(f"Placing {len(infos)} captions on video track {track}...")
    placed = pool.AppendToTimeline(infos) or []
    styled = 0
    for n, (item, c) in enumerate(zip(placed, which)):
        if item is None:
            continue
        text = c.text
        if style_item(item, text, c.color if (colors and c.color) else None, size):
            styled += 1
        if progress and n % 25 == 0:
            progress(f"Styling caption {n + 1} of {len(placed)}...")
    if not placed:
        raise ResolveError("Resolve didn't place the captions (AppendToTimeline returned nothing). Make sure the "
                           "timeline is open on the Edit page and try again.")
    return {"placed": len(placed), "styled": styled, "track": track}
