"""Extract speech (Demucs) with a stand-in for demucs: the voice file replaces the WAV; without
demucs, or when it fails, the WAV is left as it is and the reason is logged."""
import os
import subprocess
import sys
import textwrap


def _fake_demucs(tmp_path, ok=True):
    """A Python with a fake demucs.separate that writes vocals.wav where the real one would."""
    pkg = tmp_path / "site" / "demucs"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "separate.py").write_text(textwrap.dedent(f'''
        import os, subprocess, sys
        args = sys.argv[1:]
        out = args[args.index("-o") + 1]; src = args[-1]
        d = os.path.join(out, "htdemucs", os.path.splitext(os.path.basename(src))[0]); os.makedirs(d)
        if {ok!r}:
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=1",
                            "-ar", "44100", "-ac", "2", os.path.join(d, "vocals.wav")], check=True)
            print("done")
        else:
            print("CUDA out of memory"); sys.exit(1)
    '''))
    return str(tmp_path / "site")


def test_extract_replaces_the_wav_with_the_voice(tmp_path, monkeypatch):
    import shutil
    if not shutil.which("ffmpeg"):
        return
    from transcriber import speech
    wav = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-ar", "16000", "-ac", "1", str(wav)], check=True)
    before = wav.read_bytes()
    monkeypatch.setenv("PYTHONPATH", _fake_demucs(tmp_path))
    log = []
    assert speech.extract(str(wav), log.append, python=sys.executable)
    assert wav.read_bytes() != before and os.path.getsize(wav) > 1000
    assert log[-1] == "Voice extracted."
    # the output is 16 kHz mono again (what Whisper wants)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=sample_rate,channels", "-of", "csv=p=0", str(wav)],
                           capture_output=True, text=True).stdout.strip()
    assert probe == "16000,1"


def test_extract_leaves_the_wav_alone_when_demucs_fails_or_is_missing(tmp_path, monkeypatch):
    from transcriber import speech
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF....")
    monkeypatch.setenv("PYTHONPATH", _fake_demucs(tmp_path, ok=False))
    log = []
    assert not speech.extract(str(wav), log.append, python=sys.executable)
    assert wav.read_bytes() == b"RIFF...." and "CUDA out of memory" in log[-1]
    monkeypatch.setattr(speech, "python_for", lambda: None)
    assert not speech.extract(str(wav), log.append) and "pip install demucs" in log[-1]
