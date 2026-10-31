"""The parts of the NVIDIA models' worker that don't need the models: cutting audio into pieces,
token pieces into words, words into lines, and finding where one person talks alone."""

import numpy as np

from transcriber import nvidia_worker as nv


def test_chunks_cut_at_quiet_moments():
    rate = 16000
    audio = np.random.default_rng(0).standard_normal(rate * 150).astype(np.float32) * 0.1
    audio[rate * 55:rate * 56] *= 0.001                    # a quiet second near the first cut
    pieces = nv.chunks(audio, rate)
    assert pieces[0][0] == 0 and pieces[-1][1] == len(audio)
    assert all(a < b for a, b in pieces) and all(pieces[i][1] == pieces[i + 1][0] for i in range(len(pieces) - 1))
    assert all(b - a <= nv.CHUNK_SECONDS * rate for a, b in pieces)
    assert 55 * rate <= pieces[0][1] <= 56 * rate          # cut in the quiet part
    assert nv.chunks(audio[:rate * 10], rate) == [(0, rate * 10)]


def test_token_pieces_become_words():
    stamps = [{"token": "M", "start": 0.24, "end": 0.32}, {"token": "ister", "start": 0.32, "end": 0.48},
              {"token": " Qu", "start": 0.5, "end": 0.6}, {"token": "ilter", "start": 0.6, "end": 0.8},
              {"token": "'s", "start": 0.8, "end": 0.9}, {"token": ",", "start": 0.9, "end": 0.9},
              {"token": " here", "start": 1.0, "end": 1.3}, {"token": ".", "start": 1.3, "end": 1.3}]
    words = nv.words_from_tokens(stamps, offset=10.0)
    assert [w for _, _, w in words] == [" Mister", " Quilter's,", " here."]
    assert words[0][:2] == (10.24, 10.48) and words[1][:2] == (10.5, 10.9)


def test_words_become_lines():
    words = [(0.0, 0.3, " Hello"), (0.3, 0.6, " there,"), (0.6, 0.9, " Mel."), (1.0, 1.2, " Ready"),
             (1.2, 1.4, " to"), (1.4, 1.6, " go?"), (3.0, 3.2, " Yes")]
    lines = nv.lines_from_words(words)
    assert [t for _, _, t, _ in lines] == ["Hello there, Mel.", "Ready to go?", "Yes"]
    assert lines[0][:2] == (0.0, 0.9) and lines[2][1] >= lines[2][0] + 0.2


def test_long_run_breaks_at_a_comma():
    words = [(i * 0.3, i * 0.3 + 0.25, f" w{i}" + ("," if i == 14 else "")) for i in range(40)]
    lines = nv.lines_from_words(words)
    assert lines[0][2].endswith("w14,")
    assert all(len(ws) <= nv.LINE_MAX_WORDS for *_, ws in lines)


def test_solo_spans_skip_overlap():
    turns = [(0, 5, "A"), (4, 12, "B"), (11.5, 30, "A")]
    spans = nv.solo_spans(turns, "A")
    assert (12, 22.0) in spans and (22.0, 30) in spans and (0, 4) in spans
    assert all(not (a < 5 and b > 4) or b <= 4 for a, b in spans)
    assert nv.solo_spans(turns, "B") == [(5, 11.5)]
