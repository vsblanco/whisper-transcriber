# Whisper Transcriber

A small Windows desktop app that turns video and audio files into timestamped transcripts using [OpenAI Whisper](https://github.com/openai/whisper). Everything runs locally on your PC; nothing is uploaded.

- Drag in videos or audio (MP4, MKV, MOV, MP3, WAV, M4A and more), or a whole folder
- Queue several files and watch the transcript appear line by line
- Save as timestamped text (`.txt`), subtitles (`.srt`) or web subtitles (`.vtt`)
- Saves to `Documents\Transcripts` by default, or any folder you pick
- Remembers your model, language, format and folder choices
- Mixes every audio track in a file, so OBS recordings with separate mic and game audio are fully captured
- Uses your NVIDIA GPU automatically when available (much faster)

## Setup

1. **Python 3.9 to 3.12** from [python.org](https://www.python.org/downloads/). Check "Add Python to PATH" during install.
2. **ffmpeg**: open PowerShell and run
   ```
   winget install Gyan.FFmpeg
   ```
   then close and reopen PowerShell.
3. **Python packages**: in this folder, run
   ```
   pip install -r requirements.txt
   ```
4. **Optional, for NVIDIA GPUs**: install the CUDA build of PyTorch for a big speedup. Pick your options at [pytorch.org](https://pytorch.org/get-started/locally/) and run the command it gives you.

## Use it

**Desktop app:** double-click `Transcriber.bat`. Add files, choose a model, and click **Transcribe**. Double-click a finished file in the list to open its transcript.

**Drag and drop, no window:** drop files onto `Transcribe Video.bat`. It uses the medium model and saves to `Documents\Transcripts`.

**Command line:**
```
python transcribe_video.py "video.mp4"
python transcribe_video.py "a.mp4" "b.mp3" --model turbo --formats txt srt
python transcribe_video.py "talk.mkv" --language en --output-dir "D:\Transcripts"
```

## Models

| Model | Speed | Accuracy | Good for |
|---|---|---|---|
| tiny | Fastest | Rough | Quick checks |
| base | Fast | Fair | Clear speech, one speaker |
| small | Moderate | Good | Everyday use on CPU |
| medium | Slow | Very good | Default; interviews and lectures |
| large | Slowest | Best | Hard audio, accents, noisy rooms |
| turbo | Fast | Near large | Best all-round choice on a GPU |

Each model downloads once (75 MB for tiny up to about 3 GB for large) and is reused after that.

## Output

`.txt` files keep the original layout: a header, one timestamped line per segment, then the full text.

```
Transcript of: C:\Users\you\Videos\talk.mp4
Model: medium
Language: en
============================================================

[00:00:00] Alright, so we are officially recording.
[00:00:05] Thanks for joining.
...
```

If a transcript with the same name already exists, the new one is saved as `name (2)` instead of overwriting it.

## Project layout

```
app.py                 Desktop app (Tkinter)
transcribe_video.py    Command-line tool
transcriber/core.py    Audio extraction, Whisper, and .txt/.srt/.vtt writers
Transcriber.bat        Opens the app
Transcribe Video.bat   Drag-and-drop transcription without the app
```
