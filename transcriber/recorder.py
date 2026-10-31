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

    def heard(self) -> tuple[float, float]:
        """(loudest level in dBFS, seconds that sound like someone talking), before any boost."""
        import numpy as np
        audio = np.concatenate(self.chunks) if self.chunks else np.zeros(0, "float32")
        return talk_stats(audio, RATE)

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


class FileRecorder:
    """A long recording (to transcribe), written to a 48 kHz mono WAV as it goes, so an hour-long
    call doesn't have to fit in memory. Several start together on one `go` event (the mic and PC
    audio, each its own track) and stay in step with the clock: if a device ever sends nothing for a
    while, the gap is filled with silence."""

    def __init__(self, kind: str, device_id: Optional[str], path: str, go: threading.Event):
        self.kind, self.device_id, self.path, self.go = kind, device_id, path, go
        self.track_name = {"mic": "Mic", "pc": "PC audio"}.get(kind, kind)
        self.error: Optional[str] = None
        self.peak_db = -120.0
        self.talk_seconds = 0.0
        self.ready = threading.Event()             # the device opened (or failed)
        self._level = 0.0
        self._written = 0
        self._t0: Optional[float] = None
        self._stop_at: Optional[float] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._wav = None
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

    def _write(self, mono):
        """(holding the lock) Append samples, and track the volume and how much sounds like talking."""
        import numpy as np
        self._wav.writeframes((np.clip(mono, -1, 1) * 32767).astype("<i2").tobytes())
        self._written += len(mono)
        if len(mono):
            rms = float(np.sqrt(np.mean(mono ** 2)))
            self._level = min(1.0, rms * 6)
            db = 20 * np.log10(float(np.max(np.abs(mono))) + 1e-9)
            self.peak_db = max(self.peak_db, db)
            if 20 * np.log10(rms + 1e-9) > -45:
                self.talk_seconds += len(mono) / RATE

    def _pad_to(self, t: float):
        import numpy as np
        missing = int(t * RATE) - self._written
        if missing > RATE // 2:
            self._wav.writeframes(np.zeros(missing, "<i2").tobytes())
            self._written += missing

    def _run(self):
        import time
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0)   # each thread needs COM for Windows audio
        try:
            _numpy_shim()
            wav = wave.open(self.path, "wb")
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            self._wav = wav
            with self._device().recorder(samplerate=RATE, blocksize=1024) as rec:
                self.ready.set()
                self.go.wait()
                self._t0 = time.monotonic()
                rec.record(numframes=256)                  # (drop what was buffered before the start)
                while not self._stop.is_set():
                    block = rec.record(numframes=RATE // 10)
                    mono = block.mean(axis=1) if block.ndim > 1 else block
                    with self._lock:
                        if self._wav is None:
                            break
                        # behind the clock (the device sent nothing for a while): fill with silence
                        self._pad_to(time.monotonic() - self._t0 - len(mono) / RATE)
                        self._write(mono.astype("float32"))
        except Exception as e:
            self.error = str(e) or type(e).__name__
        finally:
            self.ready.set()
            self._close()

    def _close(self):
        with self._lock:
            if self._wav is None:
                return
            if self._t0 is not None and self._stop_at is not None:
                self._pad_to(self._stop_at - self._t0)    # every track ends at the same moment
            try:
                self._wav.close()
            except Exception:
                pass
            self._wav = None
            self._level = 0.0

    def level(self) -> float:
        return self._level

    def stop(self):
        import time
        self._stop_at = time.monotonic()
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._close()                       # (if the device hung, finish the file from here)


class AppRecorder(FileRecorder):
    """One app's sound (app_audio.py: Windows per-app loopback, through proc-tap), written like
    FileRecorder. An app sends nothing while it's quiet, so the track is kept in step with the
    clock by filling those stretches with silence."""

    def __init__(self, pid: int, name: str, path: str, go: threading.Event):
        super().__init__("app", None, path, go)
        self.pid, self.track_name = pid, name

    def _run(self):
        import queue
        import time
        import numpy as np
        chunks: "queue.Queue[bytes]" = queue.Queue()
        cap = None
        try:
            from proctap import ProcessAudioCapture        # 48 kHz, stereo, float32
            wav = wave.open(self.path, "wb")
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            self._wav = wav
            cap = ProcessAudioCapture(self.pid, on_data=lambda data, _n: chunks.put(data))
            cap.start()
            self.ready.set()
            self.go.wait()
            self._t0 = time.monotonic()
            while not chunks.empty():                      # (drop what came before the start)
                chunks.get_nowait()
            while not self._stop.is_set():
                try:
                    data = chunks.get(timeout=0.1)
                except queue.Empty:
                    data = None
                with self._lock:
                    if self._wav is None:
                        break
                    if data:
                        a = np.frombuffer(data, dtype=np.float32)
                        mono = a.reshape(-1, 2).mean(axis=1) if len(a) % 2 == 0 else a
                        self._pad_to(time.monotonic() - self._t0 - len(mono) / RATE)
                        self._write(mono)
                    else:                                  # the app is quiet: keep up with the clock
                        self._pad_to(time.monotonic() - self._t0 - 0.3)
                        self._level = 0.0
        except Exception as e:
            self.error = str(e) or type(e).__name__
        finally:
            if cap is not None:
                try:
                    cap.stop()
                except Exception:
                    pass
            self.ready.set()
            self._close()


def save_recording(tracks: list, dest: str) -> None:
    """Combine recorded tracks ([(wav path, name)]) into one audio file (AAC in an .m4a), each its
    own named track, like an OBS recording."""
    import subprocess
    from transcriber.core import _NO_WINDOW
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for path, _name in tracks:
        cmd += ["-i", path]
    for i in range(len(tracks)):
        cmd += ["-map", f"{i}:a"]
    cmd += ["-c:a", "aac", "-b:a", "128k"]
    for i, (_path, name) in enumerate(tracks):
        cmd += [f"-metadata:s:a:{i}", f"title={name}", f"-metadata:s:a:{i}", f"handler_name={name}"]
    cmd += ["-movflags", "+faststart", dest]
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "ffmpeg failed").strip().splitlines()[-1])


MIN_TALK = 2.0         # seconds of talking a voice sample needs


def talk_stats(audio, rate: int) -> tuple[float, float]:
    """(loudest level in dBFS, seconds of it that sound like talking) for mono samples in -1..1.
    Talking = louder than -50 dBFS and clearly above the background noise."""
    import numpy as np
    audio = np.asarray(audio, dtype="float64")
    hop = max(int(rate * 0.03), 1)
    n = len(audio) // hop
    if n == 0:
        return -120.0, 0.0
    frames = audio[: n * hop].reshape(n, hop)
    db = 20 * np.log10(np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-9)
    floor = float(np.percentile(db, 10))             # the background noise (or, with nonstop talk, the talk)
    talk = db > max(-50.0, min(floor + 10, -35.0))
    peak = 20 * np.log10(float(np.max(np.abs(audio))) + 1e-9)
    return peak, float(talk.sum()) * hop / rate


def problem_with(peak_db: float, talk_seconds: float) -> str:
    """Why a recording can't be a voice sample ('' if it's fine)."""
    if peak_db < -60:
        return "No sound came in. Pick another device above, or check it isn't muted."
    if talk_seconds < MIN_TALK:
        return "Hardly any talking was heard. Try again a little louder or longer."
    return ""


def wav_talk(path: str) -> tuple[float, float]:
    """talk_stats() of a 16-bit WAV file."""
    import numpy as np
    with wave.open(path, "rb") as w:
        rate, ch = w.getframerate(), w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype("float32") / 32768
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return talk_stats(data, rate)


def save_clip(source: str, start: float, end: float, stream: Optional[int], dest: str) -> float:
    """Cut start..end of a recording's audio (one track) into a 16 kHz mono WAV. Returns the
    seconds of talking in it (raises if ffmpeg can't)."""
    import subprocess
    from transcriber.core import _NO_WINDOW
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{max(start, 0):.3f}", "-t", f"{max(end - start, 0.5):.3f}",
           "-i", source, "-map", f"0:a:{stream or 0}", "-ac", "1", "-ar", str(OUT_RATE), dest]
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "ffmpeg failed").strip().splitlines()[-1])
    return wav_talk(dest)[1]


def voiceprint_from_wav(wav_path: str, token: str, log: Callable[[str], None] = print) -> list:
    """The voice of whoever talks most in the recording, in the same form transcripts use."""
    from transcriber.speakers import diarize
    turns, prints = diarize(wav_path, token, log, model="pyannote")
    if not turns or not prints:
        raise RuntimeError("No speech was found in the recording. Try again a little louder or longer.")
    talk: dict[str, float] = {}
    for t0, t1, who in turns:
        talk[who] = talk.get(who, 0.0) + (t1 - t0)
    best = max(talk, key=talk.get)
    if best not in prints:
        raise RuntimeError("Couldn't read a voice from the recording. Try again.")
    return prints[best]


def play_wav(path: str) -> Callable[[], None]:
    """Start playing a WAV file; returns a function that stops it."""
    import subprocess
    if sys.platform == "win32":
        import winsound
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        return lambda: winsound.PlaySound(None, 0)
    if sys.platform == "darwin":
        p = subprocess.Popen(["afplay", path])
    else:
        try:
            import vlc
            mp = vlc.MediaPlayer(path)
            mp.play()
            return mp.stop
        except Exception:
            p = subprocess.Popen(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path])
    return lambda: p.poll() is None and p.terminate()
