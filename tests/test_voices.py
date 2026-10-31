import json
import math

import numpy as np

from transcriber import people as P
from transcriber.recorder import MIN_TALK, problem_with, talk_stats


def _people(tmp_path, monkeypatch, data):
    monkeypatch.setattr(P, "VOICE_DIR", tmp_path / "voices")
    f = tmp_path / "people.json"
    f.write_text(json.dumps({"version": 1, "people": data}), encoding="utf-8")
    return P.People(f)


def test_silence_is_not_a_voice_sample():
    rate = 16000
    assert problem_with(*talk_stats(np.zeros(rate * 6), rate))                 # nothing came in
    hiss = np.random.default_rng(0).normal(0, 0.0005, rate * 6)
    assert problem_with(*talk_stats(hiss, rate))                               # background noise only
    t = np.arange(rate * 6) / rate
    talk = 0.3 * np.sin(2 * np.pi * 200 * t) * (np.sin(2 * np.pi * 0.7 * t) > -0.5) + hiss
    peak, secs = talk_stats(talk, rate)
    assert secs >= MIN_TALK and problem_with(peak, secs) == ""


def test_broken_and_copied_samples_are_cleaned_up(tmp_path, monkeypatch):
    a, b, c = [1.0, 0.0, 0.1], [0.0, 1.0, 0.1], [0.9, 0.1, 0.1]
    ppl = _people(tmp_path, monkeypatch, [
        {"id": "x", "name": "Omar", "voices": [[math.nan] * 3, [0.2, 0.2, 0.9]]},
        {"id": "m", "name": "Mel", "voices": [a, c, b, b]},          # b is Charles's, and twice
        {"id": "c", "name": "Charles", "voices": [b, [0.1, 0.9, 0.1]]},
    ])
    assert ppl.get("x")["voices"] == [[0.2, 0.2, 0.9]]
    assert ppl.get("m")["voices"] == [a, c]
    assert ppl.get("c")["voices"] == [b, [0.1, 0.9, 0.1]]
    assert all(len(p["voice_info"]) == len(p["voices"]) for p in ppl.people)


def test_add_remove_and_fit(tmp_path, monkeypatch):
    ppl = _people(tmp_path, monkeypatch, [{"id": "v", "name": "Victor", "voices": []}])
    assert not ppl.add_voice("v", [0.0, 0.0, 0.0])                             # silence: not kept
    for vp in ([1, 0.1, 0], [0.95, 0.15, 0], [1, 0.05, 0.05], [0, 0, 1]):
        assert ppl.add_voice("v", vp, {"from": "test"})
    assert ppl.add_voice("v", [1, 0.1, 0])                                     # same one again: no copy
    assert len(ppl.get("v")["voices"]) == 4
    fit = ppl.voice_fit("v")
    assert fit[3] < P.ODD_ONE_OUT < min(fit[:3])
    ppl.remove_voice("v", 3)
    assert len(ppl.voice_samples("v")) == 3
    ppl.forget_voice([1, 0.1, 0], keep_pid="someone else")
    assert len(ppl.voice_samples("v")) == 2
