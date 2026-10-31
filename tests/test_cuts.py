from transcriber.cuts import concat_list, keep_from_drop, merge_spans, remap_cues, remap_time, total


def test_spans_are_padded_joined_and_clipped():
    assert merge_spans([(10, 12), (12.2, 14), (30, 31)], pad=0.3, duration=31.1) == [(9.7, 14.3), (29.7, 31.1)]
    assert merge_spans([(5, 5.1)], pad=0.0) == []                       # a sliver is dropped
    assert keep_from_drop([(2, 4), (3, 5), (9, 20)], duration=10.0) == [(0.0, 2.0), (5.0, 9.0)]
    assert total([(0.0, 2.0), (5.0, 9.0)]) == 6.0
    assert remap_time(6.0, [(0.0, 2.0), (5.0, 9.0)]) == 3.0 and remap_time(3.0, [(0.0, 2.0), (5.0, 9.0)]) is None


def test_captions_move_onto_the_cut_timeline():
    from transcriber.captions import Cue
    cues = [Cue(1.0, 3.0, ["kept whole"]), Cue(4.0, 6.0, ["across a cut"]), Cue(7.0, 8.0, ["gone"]),
            Cue(9.5, 10.5, ["in the second part"], layer=1)]
    spans = [(0.0, 5.0), (9.0, 12.0)]
    moved = remap_cues(cues, spans)
    assert [(c.start, c.end, c.text) for c in moved] == [(1.0, 3.0, "kept whole"), (4.0, 5.0, "across a cut"),
                                                         (5.5, 6.5, "in the second part")]
    assert moved[-1].layer == 1                                          # nothing else about a caption changes
    assert concat_list(["/a/p1.mp4", "/b/it's.mp4"]) == "file '/a/p1.mp4'\nfile '/b/it'\\''s.mp4'\n"


def test_cut_video_end_to_end(tmp_path):
    """Two parts of a generated video, joined, with the captions inside as a track timed to the cut."""
    import shutil
    import subprocess
    from transcriber.captions import Cue, to_srt
    from transcriber.cuts import concat_command, remap_cues
    from transcriber.review_ui import ReviewMixin
    from transcriber.subtitle_track import mux_command
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return
    src = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=20",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=20", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    spans = [(2.0, 5.0), (10.0, 14.0)]
    parts = []
    for k, (a, b) in enumerate(spans, 1):
        part = tmp_path / f"part{k}.mp4"
        r = subprocess.run(ReviewMixin.clip_command(str(src), a, b, str(part), True), capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-300:]
        parts.append(str(part))
    (tmp_path / "parts.txt").write_text("".join(f"file '{p}'\n" for p in parts))
    joined = tmp_path / "joined.mp4"
    r = subprocess.run(concat_command(str(tmp_path / "parts.txt"), str(joined)), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-300:]
    cues = remap_cues([Cue(3.0, 4.0, ["first"]), Cue(11.0, 12.0, ["second"])], spans)
    (tmp_path / "cut.srt").write_text(to_srt(cues), encoding="utf-8")
    out = tmp_path / "out.mp4"
    r = subprocess.run(mux_command(str(joined), str(tmp_path / "cut.srt"), str(out), "english"), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-300:]
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout
    assert "subtitle" in probe
    dur = float([x for x in probe.split() if x.replace(".", "").isdigit()][-1])
    assert abs(dur - 7.0) < 0.3                                          # 3 s + 4 s
    assert "00:00:01,000 --> 00:00:02,000" in (tmp_path / "cut.srt").read_text()   # 3.0 in part one -> 1.0
    assert "00:00:04,000 --> 00:00:05,000" in (tmp_path / "cut.srt").read_text()   # 11.0 -> 3 + 1
