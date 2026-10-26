"""Speaker detection with pyannote: "who spoke when", plus a voiceprint per speaker.

Used two ways:
- inside the app, when pyannote.audio is installed in the app's own Python (the Python version);
- as a separate program, run by the speaker detection add-on's own Python (the installed app,
  which doesn't carry PyTorch):  python diarize_worker.py audio.wav
  The Hugging Face token comes in the HF_TOKEN environment variable. It prints "LOG: ..." lines
  while it works and one "RESULT: {json}" line at the end.

It imports nothing from the rest of the app, so it can be copied next to the add-on's Python."""

from __future__ import annotations

import json
import os
import sys
import wave
from typing import Callable

MODEL_ID = "pyannote/speaker-diarization-community-1"
_pipeline = None


def model_downloaded() -> bool:
    """True if the speaker model is already on this computer (then no token is needed)."""
    try:
        from huggingface_hub import try_to_load_from_cache
        return isinstance(try_to_load_from_cache(MODEL_ID, "config.yaml"), str)
    except Exception:
        return False


def _from_pretrained(token):
    from pyannote.audio import Pipeline
    try:
        return Pipeline.from_pretrained(MODEL_ID, token=token or None)
    except TypeError:               # older pyannote.audio versions call it use_auth_token
        return Pipeline.from_pretrained(MODEL_ID, use_auth_token=token or None)


def _offline(on: bool):
    """Tell huggingface_hub to use only the local copy (no network check, no token)."""
    try:
        import huggingface_hub.constants as c
    except Exception:
        c = None
    if on:
        os.environ["HF_HUB_OFFLINE"] = "1"
    else:
        os.environ.pop("HF_HUB_OFFLINE", None)
    if c is not None:
        c.HF_HUB_OFFLINE = on


def load_pipeline(token: str, log: Callable[[str], None]):
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    import torch
    pipe = None
    if model_downloaded():
        log("Loading the speaker model...")
        _offline(True)
        try:
            pipe = _from_pretrained(None)
        except Exception:
            pipe = None             # local copy incomplete: download again below
        finally:
            _offline(False)
    if pipe is None:
        if not token:
            raise RuntimeError("The speaker model isn't downloaded yet. Add your Hugging Face token in "
                               "Settings > Speakers for the first download.")
        log("Downloading the speaker model (only this once)...")
        pipe = _from_pretrained(token)
    if pipe is None:
        raise RuntimeError("Couldn't download the speaker model. Check your Hugging Face token, and that "
                           f"you accepted the terms at huggingface.co/{MODEL_ID}.")
    if torch.cuda.is_available():
        pipe.to(torch.device("cuda"))
    _pipeline = pipe
    return pipe


def _read_wav(path: str):
    """The 16 kHz mono WAV made for Whisper, as the tensor pyannote expects (skips its own decoder)."""
    import numpy as np
    import torch
    with wave.open(path, "rb") as w:
        rate, frames, width = w.getframerate(), w.readframes(w.getnframes()), w.getsampwidth()
    data = np.frombuffer(frames, dtype=np.int16 if width == 2 else np.int32).astype("float32")
    data /= 32768.0 if width == 2 else 2147483648.0
    return {"waveform": torch.from_numpy(data).unsqueeze(0), "sample_rate": rate}


def run(wav_path: str, token: str, log: Callable[[str], None] = print) -> tuple[list, dict]:
    """(turns, voiceprints): turns = [(start, end, "SPEAKER_00"), ...], voiceprints = {label: [floats]}."""
    pipe = load_pipeline(token, log)
    log("Detecting speakers...")
    output = pipe(_read_wav(wav_path))
    annotation = getattr(output, "speaker_diarization", output)   # pyannote 4 wraps the result
    turns = [(float(seg.start), float(seg.end), str(label))
             for seg, _, label in annotation.itertracks(yield_label=True)]
    prints: dict = {}
    embeddings = getattr(output, "speaker_embeddings", None)
    if embeddings is not None:
        for label, vec in zip(annotation.labels(), embeddings):
            prints[str(label)] = [float(x) for x in vec]
    return turns, prints


def _main(argv) -> int:
    if len(argv) < 2:
        print("usage: diarize_worker.py audio.wav", file=sys.stderr)
        return 2
    def log(msg):
        print("LOG: " + str(msg), flush=True)
    try:
        turns, prints = run(argv[1], os.environ.get("HF_TOKEN", ""), log)
    except Exception as exc:
        print("ERROR: " + (str(exc) or type(exc).__name__), flush=True)
        return 1
    print("RESULT: " + json.dumps({"turns": turns, "prints": prints}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
