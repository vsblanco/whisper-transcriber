"""NVIDIA's speech models, through Hugging Face Transformers:

- Parakeet TDT 0.6B v3 (nvidia/parakeet-tdt-0.6b-v3): speech to text, with a time for every word.
  English and 24 European languages (it tells them apart by itself). Fast, and it doesn't make up
  words in silence the way Whisper can.
- Nemotron 3 Diarization (nvidia/Nemotron-3-Diarization): "who spoke when", up to 8 people, and it
  notices people talking over each other. The voiceprints that match speakers to People still come
  from pyannote's voice model, so the people you've already taught keep being recognized.

Both download once from Hugging Face (Parakeet about 2.5 GB, Nemotron about 0.4 GB) and run on the
NVIDIA graphics card when PyTorch can use it, otherwise (slower) on the CPU.

Used two ways, like diarize_worker.py:
- inside the app (the Python version, with torch and transformers installed);
- as a separate program in the speaker detection add-on's Python (the installed app):
      python nvidia_worker.py transcribe audio.wav     (or: diarize audio.wav)
  It prints "LOG: ..." lines, "SEGMENT: {json}" for each line as it's recognized, and one
  "RESULT: {json}" line at the end. The Hugging Face token comes in HF_TOKEN.

It imports nothing from the rest of the app, so it can be copied next to the add-on's Python."""

from __future__ import annotations

import json
import os
import sys
import threading
import wave
from typing import Callable, Optional

PARAKEET_ID = "nvidia/parakeet-tdt-0.6b-v3"
NEMOTRON_ID = "nvidia/Nemotron-3-Diarization"
RATE = 16000

CHUNK_SECONDS = 60.0         # Parakeet hears the audio in pieces of up to a minute...
SEARCH_SECONDS = 12.0        # ...cut at the quietest moment in the last few seconds of each piece
LINE_MAX_SECONDS = 9.0
LINE_MAX_WORDS = 24
LINE_PAUSE = 0.8             # a pause this long starts a new line

_models: dict = {}
_lock = threading.Lock()


class Cancelled(Exception):
    """The transcription was cancelled."""


# ---------------------------------------------------------------- setup

def missing() -> str:
    """'' when everything needed is installed, otherwise what's missing (without importing torch)."""
    import importlib.util
    try:
        if importlib.util.find_spec("torch") is None:
            return "PyTorch isn't installed."
        if importlib.util.find_spec("transformers") is None:
            return "Transformers isn't installed."
        if importlib.util.find_spec("transformers.models.nemotron3_diarization") is None:
            return "Transformers is too old for the NVIDIA models (version 5.21 or newer is needed)."
    except (ImportError, ValueError):
        return "Transformers isn't installed."
    return ""


def downloaded(model_id: str) -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache
        return isinstance(try_to_load_from_cache(model_id, "config.json"), str)
    except Exception:
        return False


def is_loaded(kind: str) -> bool:
    return kind in _models


def _device():
    """(device, dtype): the NVIDIA card in bfloat16 (half the memory, same results) or the CPU."""
    import torch
    if torch.cuda.is_available():
        bf16 = getattr(torch.cuda, "is_bf16_supported", lambda: False)()
        return "cuda", torch.bfloat16 if bf16 else torch.float16
    return "cpu", torch.float32


def _from_pretrained(cls, model_id, token, **kw):
    """Load from the local copy when there is one (works offline, no token), else download."""
    if downloaded(model_id):
        try:
            return cls.from_pretrained(model_id, local_files_only=True, **kw)
        except Exception:
            pass                          # incomplete local copy: download again
    return cls.from_pretrained(model_id, token=token or None, **kw)


def load(kind: str, token: str = "", log: Callable[[str], None] = print):
    """(processor, model, device) for "parakeet" or "nemotron", loaded once."""
    with _lock:
        if kind in _models:
            return _models[kind]
        problem = missing()
        if problem:
            raise RuntimeError(problem + " Install it from Settings > What's installed (NVIDIA models).")
        import torch
        from transformers import AutoProcessor
        model_id = PARAKEET_ID if kind == "parakeet" else NEMOTRON_ID
        device, dtype = _device()
        where = f"the GPU ({torch.cuda.get_device_name(0)})" if device == "cuda" else "the CPU"
        name = "Parakeet" if kind == "parakeet" else "Nemotron speaker model"
        log(f"Loading NVIDIA {name} on {where}..." if downloaded(model_id) else
            f"Downloading NVIDIA {name} (first time only, {'about 2.5 GB' if kind == 'parakeet' else 'about 400 MB'})...")
        processor = _from_pretrained(AutoProcessor, model_id, token)
        if kind == "parakeet":
            from transformers import AutoModelForTDT as Auto
        else:
            from transformers import AutoModelForAudioFrameClassification as Auto
        model = _from_pretrained(Auto, model_id, token, dtype=dtype)
        model.to(device).eval()
        _models[kind] = (processor, model, device)
        return _models[kind]


def unload(kind: Optional[str] = None) -> None:
    """Free the GPU memory (all models, or one)."""
    for k in ([kind] if kind else list(_models)):
        _models.pop(k, None)
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def read_wav(path: str):
    """The 16 kHz mono WAV the app makes, as float32 samples."""
    import numpy as np
    with wave.open(path, "rb") as w:
        rate, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
        frames = w.readframes(w.getnframes())
    data = np.frombuffer(frames, dtype=np.int16 if width == 2 else np.int32).astype(np.float32)
    data /= 32768.0 if width == 2 else 2147483648.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    if rate != RATE:
        raise ValueError(f"expected {RATE} Hz audio, got {rate} Hz")
    return data


# ---------------------------------------------------------------- Parakeet: speech to text

def chunks(audio, rate: int = RATE, size: float = CHUNK_SECONDS, search: float = SEARCH_SECONDS) -> list:
    """[(first sample, last sample)]: pieces of up to `size` seconds, each cut at the quietest
    tenth of a second within the last `search` seconds (so no word is cut in half)."""
    import numpy as np
    n, out, start = len(audio), [], 0
    step = int(0.1 * rate)
    while start < n:
        end = start + int(size * rate)
        if end >= n:
            out.append((start, n))
            break
        lo = max(end - int(search * rate), start + step)
        window = audio[lo:end]
        frames = len(window) // step
        if frames > 1:
            energy = (window[: frames * step].reshape(frames, step) ** 2).mean(axis=1)
            cut = lo + int(np.argmin(energy)) * step + step // 2
        else:
            cut = end
        out.append((start, cut))
        start = cut
    return out


def words_from_tokens(stamps: list, offset: float = 0.0) -> list:
    """Transformers' timestamped tokens ({"token", "start", "end"}, pieces of words) as words:
    [(start, end, " word")]. A piece that starts with a space starts a new word; punctuation stays
    with the word before it."""
    words: list = []
    for t in stamps:
        piece = str(t.get("token", "")).replace("▁", " ")
        core = piece.strip()
        if not core:
            continue
        start, end = float(t["start"]) + offset, float(t["end"]) + offset
        punctuation = not any(ch.isalnum() for ch in core)
        if words and (not piece[0].isspace() or punctuation):      # the rest of a word, or its punctuation
            a, b, w = words[-1]
            words[-1] = (a, max(b, end), w + core)
        else:
            words.append((start, max(end, start), " " + core))
    return words


def lines_from_words(words: list) -> list:
    """Words grouped into transcript lines: [(start, end, text, words)]. A line ends at the end of
    a sentence, at a pause, or when it gets long (at a comma when there is one)."""
    lines, cur = [], []

    def flush(upto=None):
        nonlocal cur
        part, cur = (cur, []) if upto is None else (cur[:upto], cur[upto:])
        if part:
            start = part[0][0]
            lines.append((start, max(part[-1][1], start + 0.2), "".join(w for _, _, w in part).strip(), part))

    for i, w in enumerate(words):
        if cur and w[0] - cur[-1][1] >= LINE_PAUSE:
            flush()
        cur.append(w)
        text = w[2].strip()
        long_enough = len(cur) >= 3 or cur[-1][1] - cur[0][0] >= 1.5
        if text[-1:] in ".?!" and long_enough:
            flush()
        elif len(cur) >= LINE_MAX_WORDS or cur[-1][1] - cur[0][0] >= LINE_MAX_SECONDS:
            comma = max((j for j, x in enumerate(cur[:-1]) if x[2].strip()[-1:] in ",;:"), default=-1)
            flush(comma + 1 if comma >= len(cur) // 3 else None)
    flush()
    return lines


def transcribe(wav_path: str, token: str = "", log: Callable[[str], None] = print,
               on_line: Optional[Callable[[tuple], None]] = None,
               should_cancel: Callable[[], bool] = lambda: False) -> list:
    """Speech to text with Parakeet: [(start, end, text, [(start, end, " word"), ...])]."""
    import torch
    processor, model, device = load("parakeet", token, log)
    audio = read_wav(wav_path)
    pieces = chunks(audio)
    out = []
    log("Transcribing with NVIDIA Parakeet...")
    for k, (a, b) in enumerate(pieces):
        if should_cancel():
            raise Cancelled()
        part = audio[a:b]
        if len(part) < RATE * 0.3 or float((part ** 2).mean()) < 1e-8:      # (silence: nothing to hear)
            continue
        inputs = processor(part, sampling_rate=RATE)
        inputs = inputs.to(device, dtype=model.dtype)
        try:
            with torch.inference_mode():
                gen = model.generate(**inputs, return_dict_in_generate=True)
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            raise RuntimeError("The graphics card ran out of memory. Close other programs using it and try again.")
        _text, stamps = processor.decode(gen.sequences, durations=gen.durations, skip_special_tokens=True)
        stamps = stamps[0] if stamps and isinstance(stamps[0], list) else stamps
        for line in lines_from_words(words_from_tokens(stamps, a / RATE)):
            out.append(line)
            if on_line:
                on_line(line)
        if len(pieces) > 3:
            log(f"Transcribing with NVIDIA Parakeet... {int((k + 1) * 100 / len(pieces))}%")
    return out


# ---------------------------------------------------------------- Nemotron: who spoke when

def speaker_logits(audio, processor, model, device):
    """The model's per-speaker scores, one row every 10 ms: (1, frames, 8). The recording goes in
    piece by piece with the same chunk and memory sizes as Transformers' whole-file mode, so a long
    recording never has to fit in the graphics card at once."""
    import torch
    from transformers.models.nemotron3_diarization.modeling_nemotron3_diarization import (
        Nemotron3DiarizationSpeakerCache)
    cfg = model.config
    modes = dict(processor.streaming_modes)
    modes["whole_file"] = (cfg.chunk_length, cfg.chunk_right_context)
    old_modes, old_mode = processor.streaming_modes, processor.streaming_mode
    processor.streaming_modes = modes
    processor.set_streaming_mode("whole_file")
    try:
        first = processor.num_samples_first_audio_chunk
        if len(audio) <= first:                          # short: all at once
            inputs = processor(audio, sampling_rate=RATE).to(device, dtype=model.dtype)
            with torch.inference_mode():
                return model(**inputs).logits.float()
        cache = Nemotron3DiarizationSpeakerCache(cfg.streaming_config, fifo_length=cfg.fifo_length,
                                                 speaker_cache_update_period=cfg.speaker_cache_update_period)

        def pieces():
            yield processor(audio[:first], sampling_rate=RATE, is_streaming=True, is_first_audio_chunk=True)
            mel = processor.num_mel_frames_per_step
            start = processor.audio_chunk_start(mel)
            while (end := start + processor.num_samples_per_audio_chunk) <= len(audio):
                yield processor(audio[start:end], sampling_rate=RATE, is_streaming=True, is_first_audio_chunk=False)
                mel += processor.num_mel_frames_per_step
                start = processor.audio_chunk_start(mel)
            yield processor(audio[start:], sampling_rate=RATE, is_streaming=True, is_first_audio_chunk=False,
                            is_last_audio_chunk=True)

        logits = []
        with torch.inference_mode():
            for inputs in pieces():
                inputs = inputs.to(device, dtype=model.dtype)
                outputs = model(**inputs, speaker_cache=cache)
                logits.append(outputs.logits.float())
                cache = outputs.speaker_cache
        return torch.cat(logits, dim=1)
    finally:
        processor.streaming_modes = old_modes
        processor.set_streaming_mode(old_mode)


def turns_from_logits(logits, processor, min_len: float = 0.15) -> list:
    """[(start, end, "SPEAKER_00")], speakers numbered in the order they first talk."""
    segs = processor.extract_speaker_dict(logits)[0]
    return [(float(s["Start"]), float(s["End"]), f"SPEAKER_{int(s['Speaker']):02d}")
            for s in segs if float(s["End"]) - float(s["Start"]) >= min_len]


def solo_spans(turns: list, label: str, min_len: float = 1.0, max_len: float = 10.0) -> list:
    """Where only this speaker talks, in pieces of 1 to 10 seconds, longest first."""
    mine = sorted((a, b) for a, b, who in turns if who == label)
    others = sorted((a, b) for a, b, who in turns if who != label)
    spans = []
    for a, b in mine:
        cuts = [(a, b)]
        for oa, ob in others:
            if ob <= a or oa >= b:
                continue
            nxt = []
            for ca, cb in cuts:
                if ob <= ca or oa >= cb:
                    nxt.append((ca, cb))
                    continue
                if oa > ca:
                    nxt.append((ca, oa))
                if ob < cb:
                    nxt.append((ob, cb))
            cuts = nxt
        for ca, cb in cuts:
            t = ca
            while cb - t >= min_len:
                spans.append((t, min(cb, t + max_len)))
                t += max_len
    return sorted(spans, key=lambda s: s[0] - s[1])


def voiceprints(audio, turns: list, token: str, log: Callable[[str], None], most: int = 24) -> dict:
    """A voiceprint per speaker from pyannote's voice model (the one People's voiceprints were made
    with), averaged over up to `most` stretches where only that person talks. {} without pyannote."""
    import numpy as np
    try:
        try:
            from transcriber.diarize_worker import load_pipeline
        except ImportError:
            from diarize_worker import load_pipeline           # (next to this file in the add-on)
        embed = load_pipeline(token, log)._embedding
    except Exception as exc:
        log(f"Couldn't make voiceprints, so speakers won't be matched to People: {exc}")
        return {}
    import torch
    prints = {}
    for label in sorted({who for _, _, who in turns}):
        spans = solo_spans(turns, label)[:most]
        if not spans:
            continue
        longest = max(int((b - a) * RATE) for a, b in spans)
        wav = np.zeros((len(spans), 1, longest), dtype=np.float32)
        mask = np.zeros((len(spans), longest), dtype=np.float32)
        for i, (a, b) in enumerate(spans):
            piece = audio[int(a * RATE):int(b * RATE)]
            wav[i, 0, :len(piece)] = piece
            mask[i, :len(piece)] = 1.0
        vecs = []
        for i in range(0, len(spans), 8):
            vecs.append(np.asarray(embed(torch.from_numpy(wav[i:i + 8]), masks=torch.from_numpy(mask[i:i + 8]))))
        vecs = np.vstack(vecs)
        vecs = vecs[~np.isnan(vecs).any(axis=1)]
        if len(vecs):
            prints[label] = [float(x) for x in vecs.mean(axis=0)]
    return prints


def diarize(wav_path: str, token: str = "", log: Callable[[str], None] = print) -> tuple[list, dict]:
    """(turns, voiceprints), like diarize_worker.run(): turns = [(start, end, "SPEAKER_00"), ...]."""
    processor, model, device = load("nemotron", token, log)
    audio = read_wav(wav_path)
    log("Detecting speakers with NVIDIA Nemotron...")
    import torch
    try:
        logits = speaker_logits(audio, processor, model, device)
    except torch.cuda.OutOfMemoryError:
        torch.cuda.empty_cache()
        raise RuntimeError("The graphics card ran out of memory. Close other programs using it and try again.")
    turns = turns_from_logits(logits, processor)
    if not turns:
        return [], {}
    return turns, voiceprints(audio, turns, token, log)


# ---------------------------------------------------------------- as a separate program

def _main(argv) -> int:
    if len(argv) < 3 or argv[1] not in ("transcribe", "diarize"):
        print("usage: nvidia_worker.py transcribe|diarize audio.wav", file=sys.stderr)
        return 2

    def log(msg):
        print("LOG: " + str(msg), flush=True)
    token = os.environ.get("HF_TOKEN", "")
    try:
        if argv[1] == "transcribe":
            def line(ln):
                print("SEGMENT: " + json.dumps([ln[0], ln[1], ln[2], ln[3]]), flush=True)
            result = {"lines": [list(x) for x in transcribe(argv[2], token, log, on_line=line)]}
        else:
            turns, prints = diarize(argv[2], token, log)
            result = {"turns": turns, "prints": prints}
    except Exception as exc:
        print("ERROR: " + (str(exc) or type(exc).__name__), flush=True)
        return 1
    print("RESULT: " + json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
