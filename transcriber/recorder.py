"""Record a short voice sample from the microphone or from PC audio (what's playing through your
speakers, e.g. a friend on Discord). Uses the `soundcard` package (pip install soundcard).

The result is a 16 kHz mono WAV, the same format Whisper and the speaker model use.
"""

from __future__ import annotations

from transcriber.fonts import PIP
import random
import sys
import threading
import wave
from typing import Callable, Optional

RATE = 48000          # what Windows devices run at; converted to 16 kHz when saving
OUT_RATE = 16000

SENTENCES = [
    "The quick brown fox jumps over the lazy dog near the riverbank.",
    "I usually grab a coffee before work, but today I made tea instead.",
    "Could you turn the music down a little? I'm trying to concentrate.",
    "We drove for six hours and stopped only once to get gas and snacks.",
    "My favorite part of the weekend is sleeping in without an alarm.",
    "Please remember to send the files before the meeting on Thursday.",
    "The weather was perfect, so we walked along the beach at sunset.",
    "Honestly, I think the sequel was better than the original movie.",
    "She counted one, two, three, four, five, and then opened her eyes.",
    "If the game crashes again, I'm going to restart the whole computer.",
    "There's a small bakery downtown that makes amazing cinnamon rolls.",
    "Let me know what time works for you, and I'll set up the call.",
    "The battery died halfway through, so we lost the last ten minutes.",
    "Every Sunday we cook a big dinner and invite whoever is around.",
    "Wait, say that again? I think my headphones cut out for a second.",
    "Thirty-seven people signed up, which is way more than we expected.",
]


def pick_sentences(n: int = 4) -> list[str]:
    return random.sample(SENTENCES, n)


def available() -> tuple[bool, str]:
    import importlib.util
    if importlib.util.find_spec("soundcard") is None:      # (works in the installed app too)
        return False, f"Recording needs the soundcard package. Run:  {PIP} install soundcard"
    return True, ""


def _numpy_shim():
    """soundcard 0.4.x calls numpy.fromstring(binary), which numpy 2 removed."""
    import numpy
    try:
        numpy.fromstring(b"\0\0\0\0", dtype="float32")
    except Exception:
        original = numpy.fromstring

        def fromstring(s, dtype=float, count=-1, sep=""):
            if sep == "" and isinstance(s, (bytes, bytearray, memoryview)):
                return numpy.frombuffer(s, dtype=dtype, count=count).copy()
            return original(s, dtype=dtype, count=count, sep=sep)
        numpy.fromstring = fromstring


def sources() -> dict[str, list[tuple[str, str]]]:
    """{"mic": [(id, name)], "pc": [(id, name)]}, default device first in each list."""
    import soundcard as sc
    mics, pcs = [], []
    try:
        default_mic = sc.default_microphone()
        mics.append((default_mic.id, f"{default_mic.name} (default)"))
    except Exception:
        default_mic = None
    for m in sc.all_microphones(include_loopback=False):
        if not default_mic or m.id != default_mic.id:
            mics.append((m.id, m.name))
    try:
        default_spk = sc.default_speaker()
        pcs.append((default_spk.name, f"{default_spk.name} (default)"))
    except Exception:
        default_spk = None
    for s in sc.all_speakers():
        if not default_spk or s.name != default_spk.name:
            pcs.append((s.name, s.name))
    return {"mic": mics, "pc": pcs}


class Recorder:
    """Records in a background thread. level() gives the live volume (0..1) for a meter."""

    def __init__(self, kind: str, device_id: Optional[str] = None):
        self.kind, self.device_id = kind, device_id
        self.chunks = []
        self.error: Optional[str] = None
        self._level = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _device(self):
        import soundcard as sc
        if self.kind == "pc":
            name = self.device_id or sc.default_speaker().name
            return sc.get_microphone(id=name, include_loopback=True)
        return sc.get_microphone(id=self.device_id) if self.device_id else sc.default_microphone()

    def _run(self):
        import numpy as np
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0)   # each thread needs COM for Windows audio
        try:
            _numpy_shim()
            with self._device().recorder(samplerate=RATE, blocksize=1024) as rec:
                while not self._stop.is_set():
                    block = rec.record(numframes=RATE // 10)
                    mono = block.mean(axis=1) if block.ndim > 1 else block
                    self.chunks.append(mono.astype("float32"))
                    self._level = float(min(1.0, np.sqrt(np.mean(mono ** 2)) * 6))
        except Exception as e:
            self.error = str(e) or type(e).__name__

    def level(self) -> float:
        return self._level

    def seconds(self) -> float:
        return sum(len(c) for c in self.chunks) / RATE

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def save_wav(self, path: str) -> float:
        """Write the recording as 16 kHz mono. Returns its length in seconds (speech-ish part)."""
        import numpy as np
        audio = np.concatenate(self.chunks) if self.chunks else np.zeros(0, "float32")
        n_out = int(len(audio) * OUT_RATE / RATE)
        if n_out:
            audio = np.interp(np.linspace(0, len(audio) - 1, n_out), np.arange(len(audio)), audio)
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        if peak > 0:
            audio = audio * min(0.9 / peak, 20.0)          # quiet PC audio gets a boost
        pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(OUT_RATE)
            w.writeframes(pcm.tobytes())
        return n_out / OUT_RATE


def voiceprint_from_wav(wav_path: str, token: str, log: Callable[[str], None] = print) -> list:
    """The voice of whoever talks most in the recording, in the same form transcripts use."""
    from transcriber.speakers import diarize
    turns, prints = diarize(wav_path, token, log)
    if not turns or not prints:
        raise RuntimeError("No speech was found in the recording. Try again a little louder or longer.")
    talk: dict[str, float] = {}
    for t0, t1, who in turns:
        talk[who] = talk.get(who, 0.0) + (t1 - t0)
    best = max(talk, key=talk.get)
    if best not in prints:
        raise RuntimeError("Couldn't read a voice from the recording. Try again.")
    return prints[best]
