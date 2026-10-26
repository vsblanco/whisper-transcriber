"""Markers out to video editors.

- DaVinci Resolve: an EDL of timeline markers (Media Pool: right-click the timeline > Timelines > Import >
  Timeline Markers from EDL).
- Final Cut Pro: FCPXML with the original file as a clip and the markers on it.
- Premiere Pro: Final Cut 7 XML (xmeml), the format Premiere imports, with the markers on the clip.

Times are counted in real frames (29.97 fps stays accurate over long recordings) and written as
non-drop-frame timecode."""

from __future__ import annotations

import subprocess
from fractions import Fraction
from typing import Optional
from xml.sax.saxutils import escape, quoteattr

from transcriber.core import _NO_WINDOW

RESOLVE_COLORS = {"you": "ResolveColorBlue", "claude": "ResolveColorYellow"}   # Resolve has no orange
PREMIERE_COLORS = {"you": "Blue", "claude": "Orange"}


def frame_rate(path: Optional[str]) -> Fraction:
    """The video's frame rate (30 for audio-only files or when ffprobe can't tell)."""
    if path:
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                  "stream=r_frame_rate", "-of", "csv=p=0", path],
                                 capture_output=True, text=True, creationflags=_NO_WINDOW).stdout.strip()
            num, _, den = out.splitlines()[0].partition("/")
            fps = Fraction(int(num), int(den or 1))
            if 10 <= fps <= 120:
                return fps
        except (OSError, ValueError, IndexError, ZeroDivisionError):
            pass
    return Fraction(30)


def label(m: dict) -> str:
    """What the marker is called in the editor: the note, else the line itself."""
    text = (m.get("note") or m.get("text") or "Marker").strip()
    return " ".join(text.split())


def _frames(sec: float, fps: Fraction) -> int:
    return int(round(Fraction(sec).limit_denominator(1000) * fps))


def _dur_frames(m: dict, fps: Fraction) -> int:
    """A range marker's length in frames (1 for a point)."""
    if m.get("end") is None:
        return 1
    return max(_frames(float(m["end"]), fps) - _frames(float(m.get("time", 0)), fps), 1)


def timecode(frames: int, fps: Fraction) -> str:
    base = round(fps)
    f = frames % base
    s = frames // base
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{f:02d}"


def to_edl(markers: list, title: str, fps: Fraction, start_hour: int = 1) -> str:
    """Resolve's timeline-marker EDL. start_hour: where the timeline's timecode starts
    (Resolve's default is 01:00:00:00)."""
    base = round(fps)
    offset = start_hour * 3600 * base
    out = [f"TITLE: {title}", "FCM: NON-DROP FRAME", ""]
    for n, m in enumerate(sorted(markers, key=lambda m: m.get("time", 0)), 1):
        a = offset + _frames(float(m.get("time", 0)), fps)
        dur = _dur_frames(m, fps)
        tc_in, tc_out = timecode(a, fps), timecode(a + dur, fps)
        color = RESOLVE_COLORS["claude" if m.get("by") == "claude" else "you"]
        name = label(m).replace("|", "/")
        out.append(f"{n:03d}  001      V     C        {tc_in} {tc_out} {tc_in} {tc_out}  ")
        out.append(f" |C:{color} |M:{name} |D:{dur}")
        out.append("")
    return "\r\n".join(out) + "\r\n"


def _file_url(path: str) -> str:
    from urllib.parse import quote
    s = str(path).replace("\\", "/")
    if s.startswith("//"):                      # network share: file://server/share/...
        return "file:" + quote(s, safe="/:")
    return "file:///" + quote(s.lstrip("/"), safe="/:")


def _base(path: str) -> str:
    return str(path).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _rt(frames: int, fps: Fraction) -> str:
    """FCPXML rational time for a number of frames."""
    t = Fraction(frames) / fps
    return f"{t.numerator}/{t.denominator}s" if t.denominator != 1 else f"{t.numerator}s"


def to_fcpxml(markers: list, title: str, source: str, fps: Fraction, duration: float,
              has_video: bool = True) -> str:
    total = max(_frames(duration, fps), 1)
    fd = _rt(1, fps)
    name = _base(source).rsplit(".", 1)[0]
    fmt = ('<format id="r1" frameDuration="%s" width="1920" height="1080"/>' % fd)
    rows = []
    for m in sorted(markers, key=lambda m: m.get("time", 0)):
        f = min(_frames(float(m.get("time", 0)), fps), total - 1)
        note = m.get("text") if m.get("note") else ""
        who = "Claude" if m.get("by") == "claude" else ""
        extra = " ".join(x for x in (who + ":" if who else "", note or "") if x).strip()
        d = min(_dur_frames(m, fps), max(total - f, 1))
        rows.append(f'            <marker start="{_rt(f, fps)}" duration="{_rt(d, fps)}" value={quoteattr(label(m))}'
                    + (f' note={quoteattr(extra)}' if extra else "") + "/>")
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!DOCTYPE fcpxml>",
        '<fcpxml version="1.9">',
        "  <resources>",
        "    " + fmt,
        f'    <asset id="r2" name={quoteattr(name)} start="0s" duration="{_rt(total, fps)}" '
        f'hasVideo="{1 if has_video else 0}" hasAudio="1" audioSources="1" audioChannels="2"'
        + (' format="r1"' if has_video else "") + ">",
        f"      <media-rep kind=\"original-media\" src={quoteattr(_file_url(source))}/>",
        "    </asset>",
        "  </resources>",
        "  <library>",
        f"    <event name={quoteattr(title)}>",
        f"      <project name={quoteattr(title + ' (markers)')}>",
        f'        <sequence format="r1" duration="{_rt(total, fps)}" tcStart="0s" tcFormat="NDF">',
        "          <spine>",
        f'          <asset-clip ref="r2" offset="0s" name={quoteattr(name)} start="0s" duration="{_rt(total, fps)}">',
        *rows,
        "          </asset-clip>",
        "          </spine>",
        "        </sequence>",
        "      </project>",
        "    </event>",
        "  </library>",
        "</fcpxml>",
        "",
    ])


def to_xmeml(markers: list, title: str, source: str, fps: Fraction, duration: float,
             has_video: bool = True) -> str:
    """Final Cut 7 XML, which Premiere Pro imports (File > Import)."""
    base = round(fps)
    ntsc = "TRUE" if fps.denominator == 1001 else "FALSE"
    total = max(_frames(duration, fps), 1)
    rate = f"<rate><timebase>{base}</timebase><ntsc>{ntsc}</ntsc></rate>"
    name = escape(_base(source))
    url = escape(_file_url(source).replace("file:///", "file://localhost/", 1))
    marks = []
    for m in sorted(markers, key=lambda m: m.get("time", 0)):
        f = min(_frames(float(m.get("time", 0)), fps), total - 1)
        comment = m.get("text") if m.get("note") else ""
        color = PREMIERE_COLORS["claude" if m.get("by") == "claude" else "you"]
        marks.append(f"<marker><name>{escape(label(m))}</name><comment>{escape(comment or '')}</comment>"
                     f"<in>{f}</in><out>{f + _dur_frames(m, fps) if m.get('end') is not None else -1}</out>"
                     f"<color>{color}</color></marker>")
    marks_xml = "".join(marks)
    file_xml = (f'<file id="file-1"><name>{name}</name><pathurl>{url}</pathurl>{rate}<duration>{total}</duration>'
                "<media>" + ("<video></video>" if has_video else "") + "<audio><channelcount>2</channelcount></audio>"
                "</media></file>")

    def clip(cid, first):
        return (f'<clipitem id="{cid}"><name>{name}</name><enabled>TRUE</enabled><duration>{total}</duration>'
                f"{rate}<start>0</start><end>{total}</end><in>0</in><out>{total}</out>"
                + (file_xml if first else '<file id="file-1"/>') + (marks_xml if first else "") + "</clipitem>")

    video = f"<video><track>{clip('clip-v', True)}</track></video>" if has_video else "<video></video>"
    audio = f"<audio><track>{clip('clip-a', not has_video)}</track></audio>"
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'
            f"<sequence id=\"seq-1\"><name>{escape(title)}</name><duration>{total}</duration>{rate}"
            f"<timecode>{rate}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>"
            f"<media>{video}{audio}</media></sequence></xmeml>\n")
