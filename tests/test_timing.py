import numpy as np

from transcriber.timing import FPS, snap


def _mask(chunks, total=20.0):
    m = np.zeros(int(total * FPS), dtype=bool)
    for a, b in chunks:
        m[int(a * FPS):int(b * FPS)] = True
    return m


def test_start_moves_to_where_the_voice_starts():
    # Whisper says the second line starts at 4.0 (when the first one ended), the voice starts at 5.0.
    m = _mask([(1.0, 3.9), (5.0, 8.0)])
    out = snap([(1.0, 4.0), (4.0, 8.0)], m, 0.0, ["one two three four five", "six seven eight nine ten"])
    assert abs(out[1][0] - 4.95) < 0.06
    assert out[0][1] <= out[1][0]


def test_offset_and_no_audio_change():
    m = _mask([(2.0, 4.0)])
    out = snap([(11.5, 14.0)], m, 10.0)          # the audio starts at 10 s on the timeline
    assert abs(out[0][0] - 11.95) < 0.06
    assert snap([], m) == []
