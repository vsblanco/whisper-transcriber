"""The copy of a video with the captions as a subtitle track."""

import json
import shutil
import subprocess

import pytest

from transcriber import subtitle_track as st

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def test_containers_and_codecs():
    assert st.output_for("a/b/game.mkv") == (".mkv", "srt")
    assert st.output_for("game.MP4") == (".mp4", "mov_text")
    assert st.output_for("game.mov") == (".mov", "mov_text")
    assert st.output_for("game.webm") == (".mkv", "srt")       # (WebM can't hold SRT)
    assert st.default_name("C:/x/Game Night.webm") == "Game Night (captions).mkv"


def test_language_codes():
    assert st.language_code("en") == "eng"
    assert st.language_code("English") == "eng"
    assert st.language_code("spa") == "spa"
    assert st.language_code("") == "und"


def test_partial_name_keeps_the_extension():
    assert st.part_path("/v/Game (captions).mkv").endswith("Game (captions).partial.mkv")


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg")
@pytest.mark.parametrize("ext,codec", [(".mkv", "subrip"), (".mp4", "mov_text")])
def test_adds_a_subtitle_track(tmp_path, ext, codec):
    src = tmp_path / f"in{ext}"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=3",
                    "-f", "lavfi", "-i", "sine=duration=3", "-f", "lavfi", "-i", "sine=frequency=300:duration=3",
                    "-map", "0", "-map", "1", "-map", "2", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(src)],
                   check=True)
    srt = tmp_path / "c.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:01,500\nVictor: Hello there\n\n"
                   "2\n00:00:01,600 --> 00:00:02,800\nCafé, ñ and emoji 🎮\n", encoding="utf-8")
    out = tmp_path / f"out{ext}"
    seen = []
    err = st.run(st.mux_command(str(src), str(srt), str(out), "en"), 3.0, seen.append)
    assert err == ""
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)],
                                     capture_output=True, text=True).stdout)
    kinds = [s["codec_type"] for s in info["streams"]]
    assert kinds.count("video") == 1 and kinds.count("audio") == 2 and kinds.count("subtitle") == 1
    sub = next(s for s in info["streams"] if s["codec_type"] == "subtitle")
    assert sub["codec_name"] == codec
    assert sub["tags"].get("language") == "eng"
    assert sub["disposition"]["default"] == 1
    text = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-map", "0:s:0", "-f", "srt", "-"],
                          capture_output=True, text=True, encoding="utf-8").stdout
    assert "Victor: Hello there" in text and "Café, ñ" in text


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg")
def test_failure_leaves_no_file(tmp_path):
    out = tmp_path / "out.mkv"
    err = st.run(st.mux_command(str(tmp_path / "missing.mkv"), str(tmp_path / "x.srt"), str(out)), None)
    assert err and not out.exists()
