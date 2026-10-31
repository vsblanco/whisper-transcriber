from transcriber.captions import MAX_CHARS, MIN_DUR, MIN_GAP, Line, break_cost, build, tidy_sounds, to_srt, to_vtt, wrap


def _words(text, start, per=0.35):
    out, t = [], start
    for w in text.split():
        out.append((t, t + per - 0.05, w))
        t += per
    return out


def test_long_line_becomes_caption_sized_pieces():
    text = ("Who older workers are, what the problem is, what they actually want from work, "
            "a solution that fits those wants, and then what to do about it.")
    w = _words(text, 10.0)
    cues = build([Line(10.0, w[-1][1], text, words=w)])
    assert len(cues) > 1
    for c in cues:
        assert len(c.lines) <= 2 and all(len(x) <= MAX_CHARS for x in c.lines)
        assert c.end - c.start <= 7.0
    assert " ".join(" ".join(c.lines) for c in cues) == text          # nothing lost or reordered
    assert cues[0].start == 10.0                                      # starts with the first word


def test_line_breaks_at_natural_places():
    assert break_cost("Victor,", "and") < break_cost("the", "game")
    assert break_cost("Ian", "Burn") > break_cost("hiring", "and")
    _c, lines = wrap("The Bureau of Labor Statistics defines older workers as anyone 55 and up.".split())
    assert all(len(x) <= MAX_CHARS for x in lines) and not lines[0].endswith((" the", " of", " as"))


def test_timing_rules():
    a = Line(1.0, 1.3, "Hi.", "A", [(1.0, 1.3, "Hi.")])
    b = Line(1.6, 3.0, "How are you doing today?", "A", _words("How are you doing today?", 1.6, 0.28))
    cues = build([a, b])
    assert cues[0].end - cues[0].start >= MIN_DUR - 1e-6 or cues[0].end == cues[1].start - MIN_GAP
    assert abs(cues[1].start - cues[0].end - MIN_GAP) < 1e-6          # short gap closed to 2 frames


def test_names_only_when_the_speaker_changes_and_dashes_for_two_at_once():
    lines = [Line(0, 1.5, "Are you going to win?", "Victor", _words("Are you going to win?", 0, 0.3)),
             Line(1.6, 3.0, "Maybe we will.", "Victor", _words("Maybe we will.", 1.6, 0.3)),
             Line(4.0, 5.2, "I hope so.", "Omar", _words("I hope so.", 4.0, 0.4), track="Discord"),
             Line(4.1, 5.0, "Nice.", "Victor", _words("Nice.", 4.1, 0.4), track="Mic")]
    from transcriber import captions as C
    C.OPTIONS["overlap"] = "dash"
    try:
        texts = [c.text for c in build(lines)]
        assert texts[0].startswith("Victor: ") and not texts[1].startswith("Victor")
        assert texts[2] == "- I hope so.\n- Nice."
        assert not any("Victor" in t for t in [c.text for c in build(lines[:2])])   # one speaker: no names
    finally:
        C.OPTIONS["overlap"] = "layer"
    # layered (the default): two captions at once, the second person's stacked above
    cues = build(lines)
    two = [c for c in cues if c.start >= 3.9]
    assert len(two) == 2
    omar, victor = sorted(two, key=lambda c: c.name)
    assert omar.layer == 0 and victor.layer == 1           # Omar started first: the usual spot
    assert "Nice." in victor.text and omar.end > 5.0        # neither cut short for the other


def test_layers_three_people_and_a_spot_picked_by_hand():
    from transcriber import captions as C
    w = lambda t, s: _words(t, s, 0.7)                     # slow talkers: all four still going at 1.5 s
    lines = [Line(0, 3.0, "First person talking here.", "A", w("First person talking here.", 0), track="a"),
             Line(0.5, 3.0, "Second person here.", "B", w("Second person here.", 0.5), track="b"),
             Line(1.0, 3.0, "Third one too.", "C", w("Third one too.", 1.0), track="c"),
             Line(1.5, 3.0, "Fourth has to wait.", "D", w("Fourth has to wait.", 1.5), track="d")]
    cues = sorted(build(lines), key=lambda c: c.name)
    assert [c.layer for c in cues[:3]] == [0, 1, 2]
    assert cues[3].start > 2.0                              # no fourth spot: waits for one to free up
    # a spot picked by hand (the timeline) is kept, and others go around it
    lines[0].layer = 2
    cues = sorted(build(lines[:3]), key=lambda c: c.name)
    assert [c.layer for c in cues] == [2, 0, 1]
    assert C.MAX_LAYERS == 3


def test_joined_sentence_and_sounds_and_files():
    a = Line(0, 1.0, "you're getting them, you're", "", _words("you're getting them, you're", 0, 0.25))
    b = Line(1.1, 1.6, "getting them.", "", _words("getting them.", 1.1, 0.25))
    cues = build([a, b])
    assert cues[-1].text.endswith("you're getting them.") and len(cues) == 1
    assert tidy_sounds("(Laughs) okay *sighs* [Music]") == "[laughs] okay [sighs] [music]"
    srt, vtt = to_srt(cues), to_vtt(build([Line(0, 1, "Hi there.", "Mel", None), Line(2, 3, "Yo.", "Cam", None)]))
    assert srt.startswith("1\n00:00:00,000 --> ") and "<v Mel>Mel: Hi there.</v>" in vtt


def test_scene_cut_moves_times_onto_it():
    w = _words("Here we go now.", 5.2, 0.3)
    c = build([Line(5.2, w[-1][1], "Here we go now.", words=w)], cuts=[5.0, 6.7])[0]
    assert c.start == 5.0 and abs(c.end - (6.7 - MIN_GAP)) < 1e-6


def test_fixed_line_keeps_the_time_set_by_hand():
    from transcriber import captions as C
    """A line stretched on the timeline stays on screen until its new end, not just until the last
    word; one not set by hand still follows its words."""
    words = [(10.0, 10.4, "Hello"), (10.4, 10.9, "there.")]
    loose = C.build([C.Line(10.0, 15.0, "Hello there.", words=words)])
    fixed = C.build([C.Line(10.0, 15.0, "Hello there.", words=words, fixed=True)])
    assert loose[0].end < 13.0
    assert fixed[0].start == 10.0 and fixed[0].end == 15.0
    long = C.build([C.Line(9.5, 25.0, "Hello there.", words=words, fixed=True)])     # beyond the 7 s rule
    assert (long[0].start, long[0].end) == (9.5, 25.0)
    nxt = C.build([C.Line(10.0, 15.0, "Hello there.", words=words, fixed=True),
                   C.Line(14.0, 16.0, "Next.", words=[(14.0, 14.5, "Next.")])])
    assert nxt[0].end <= 14.0                          # kept clear of the next caption of the same voice


def test_cues_say_which_lines_they_came_from():
    from transcriber import captions as C
    a = C.Line(0, 1.0, "you're getting them, you're", "", _words("you're getting them, you're", 0, 0.25), src="a")
    b = C.Line(1.1, 1.6, "getting them.", "", _words("getting them.", 1.1, 0.25), src="b")
    c = C.Line(5.0, 5.3, "Yeah.", "", [(5.0, 5.3, "Yeah.")], src="c")
    cues = C.build([a, b, c])
    assert cues[0].src == ["a", "b"]                   # one sentence Whisper cut in two: one caption
    assert cues[-1].src == ["c"] and cues[-1].end >= 5.0 + C.MIN_DUR - 1e-6   # kept up long enough to read


def test_word_highlight_ass_events():
    """Burned-in captions with each word lit up as it's said: one event per word, the word in the
    speaker's color; ffmpeg's subtitle renderer accepts the file."""
    from transcriber import captions as C
    lines = [Line(0, 1.6, "Are you going to win?", "Victor", _words("Are you going to win?", 0, 0.3), color="#ff0000"),
             Line(2.0, 4.7, "Maybe, if the game is long enough for us.", "Omar",
                  _words("Maybe, if the game is long enough for us.", 2.0, 0.3), color="#00aa00")]
    cues = C.build(lines)
    out = C.to_ass(cues, highlight="speaker")
    ev = [l for l in out.splitlines() if l.startswith("Dialogue")]
    assert len(ev) == 5 + 9                                      # one event per word
    assert ev[1].endswith("Are {\\c&H0000FF&}you{\\r} going to win?")   # the word, in Victor's color (BGR)
    assert ev[0].split(",")[1:3] == ["0:00:00.00", "0:00:00.30"] and ev[4].split(",")[2] == ev[4].split(",")[2]
    plain = C.to_ass(cues)
    assert len([l for l in plain.splitlines() if l.startswith("Dialogue")]) == 2
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path
    if not shutil.which("ffmpeg"):
        return
    tmp = Path(tempfile.mkdtemp())
    (tmp / "c.ass").write_text(out, encoding="utf-8")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=2",
                        "-vf", "subtitles=c.ass", "-frames:v", "5", "-f", "null", "-"], cwd=tmp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-300:]
