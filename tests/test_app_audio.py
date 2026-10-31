"""Recording one app's sound: the recorder keeps the track in step with the clock when the app is
quiet (Windows sends nothing then), and turns proc-tap's stereo float audio into the mono track."""

import sys
import threading
import time
import types
import wave

import numpy as np

from transcriber import app_audio, recorder


class FakeCapture:
    """Like proctap.ProcessAudioCapture: 48 kHz stereo float32 chunks of 10 ms while 'playing',
    nothing while quiet (here: 0.3-1.5 s in)."""

    def __init__(self, pid, on_data=None):
        self.pid, self.on_data, self._stop = pid, on_data, threading.Event()

    def start(self):
        def run():
            t0 = time.monotonic()
            while not self._stop.is_set():
                t = time.monotonic() - t0
                if not 0.3 < t < 1.5:
                    chunk = np.full((480, 2), 0.25, dtype=np.float32)
                    self.on_data(chunk.tobytes(), 480)
                time.sleep(0.01)
        threading.Thread(target=run, daemon=True).start()

    def stop(self):
        self._stop.set()


def test_app_track_keeps_time_through_quiet_parts(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "proctap", types.SimpleNamespace(ProcessAudioCapture=FakeCapture))
    go = threading.Event()
    rec = recorder.AppRecorder(1234, "Chrome", str(tmp_path / "app.wav"), go)
    rec.start()
    assert rec.ready.wait(2) and rec.error is None
    go.set()
    time.sleep(2.2)
    rec.stop()
    with wave.open(str(tmp_path / "app.wav")) as w:
        assert (w.getnchannels(), w.getframerate()) == (1, recorder.RATE)
        seconds = w.getnframes() / w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    assert 2.0 <= seconds <= 2.5                       # as long as the recording, quiet part included
    loud = np.abs(data[: int(0.25 * recorder.RATE)]).mean()
    assert loud > 0.2 * 32767                          # stereo 0.25 -> mono 0.25
    quiet = data[int(0.7 * recorder.RATE): int(1.0 * recorder.RATE)]
    assert np.abs(quiet).max() == 0                    # the quiet stretch is silence, in its place
    assert rec.track_name == "Chrome" and rec.peak_db > -20 and rec.talk_seconds > 0.5


def test_friendly_names_and_labels():
    assert app_audio.friendly("chrome.exe") == "Chrome"
    assert app_audio.friendly("msedge.exe") == "Edge"
    assert app_audio.friendly("someGame.exe") == "SomeGame"
    win = {"pid": 1, "title": "Lofi beats - YouTube - Google Chrome", "exe": "chrome.exe", "name": "Chrome"}
    assert app_audio.label(win).startswith("Chrome: Lofi beats")
    wins = [win, {"pid": 2, "title": "Discord", "exe": "Discord.exe", "name": "Discord"}]
    assert app_audio.find(wins, "discord.exe")["pid"] == 2
    assert app_audio.find(wins, "chrome.exe", "nope")["pid"] == 1
    assert app_audio.find(wins, "spotify.exe") is None
