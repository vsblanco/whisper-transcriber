# Whisper Transcriber

A desktop app for Windows and Mac that turns video and audio files into timestamped transcripts using [OpenAI Whisper](https://github.com/openai/whisper). Everything runs locally on your computer; nothing is uploaded.

- Drag in videos or audio (MP4, MKV, MOV, MP3, WAV, M4A and more), or a whole folder
- Queue several files and watch the transcript appear line by line
- Each file in the list shows a preview frame (videos) or an audio icon (audio files), plus its length
- A Transcripts tab lists everything in your transcripts folder, with search, and shows any transcript when you click it
- Transcribe a saved transcript again (select it, click Transcribe) to make a new version; every version is kept and a version picker switches between them, newest first
- Delete a single version or a whole transcript; files go to the Recycle Bin after a confirmation. Deleting on network drives (a NAS) is off unless you allow it in Settings, since those deletes are permanent
- Click the model name at the top right to switch models quickly; the new model loads in the background
- Remembers its window position and size, including on a second monitor, and keeps popups on the same screen
- The transcript panel follows whatever you select: live text for the file being transcribed, the finished text for done files
- Settings live behind a Settings button, with a one-line summary of your choices beside it
- A preview player plays the selected file with captions from its transcript; click any timestamp to jump there (needs VLC, see Setup)
- Dragging files over the list shows a drop zone
- Save as timestamped text (`.txt`), subtitles (`.srt`) or web subtitles (`.vtt`)
- Saves to `Documents\Transcripts` by default, or any folder you pick
- Remembers your model, language, format and folder choices
- Mixes every audio track in a file by default, so OBS recordings with separate mic and game audio are fully captured
- Multi-track files (like OBS recordings): when you click Transcribe, the app asks whether to transcribe each track separately or all mixed, and lets you give tracks nicknames like "Mic" or "Discord". Separate tracks are shown together in one list, in time order, with each row's track name at its right end
- **Speakers:** optionally detects who is talking and labels each line ("Speaker 1", "Speaker 2"). Click a name to say who it is; the app learns that voice and recognizes the person by name in future recordings. Names go into the saved .txt/.srt/.vtt files too. Transcripts made with detection off show a person button above the text that adds speakers later, without transcribing again
- **People:** profiles with a name and photo (shown as a circle, rounded square or square, set in Settings > Appearance), plus the voice samples learned from tagging. Choosing a photo opens a crop window (drag to position, zoom with the slider or mouse wheel); the original is kept, so **Adjust crop...** works later. Photos are cached in `%APPDATA%\WhisperTranscriber\avatars`
- **Record a voice sample:** the microphone button next to each person records them talking, from your microphone or from PC audio (a friend on Discord, a video). Sentences to read are shown if you want them, or just talk. Needs speaker detection set up, plus `pip install soundcard`
- **Change a line's time:** double-click its timestamp, type the time (like 1:23 or 0:01:23), Enter. It saves with the other changes, and lines are put back in time order
- **Fix wording in place:** double-click any row to edit its text right there (Enter or Esc to finish). Changed letters get a yellow highlight, and a bar appears under the transcript with Discard, Undo, Redo and Save (Ctrl+Z, Ctrl+Y, Ctrl+S work too). Saving updates the .txt/.srt/.vtt files
- **Right-click a row** for a menu: Edit text, Change Speaker (fixes one line without renaming that speaker everywhere), Copy text, and Delete line. Deleted lines turn red and are removed when you click Save in the bar under the transcript (Undo and Discard work for them too)
- **OBS-style Track 1:** tick "Track 1 already has everything" in the tracks window (remembered). Then "mixed" uses Track 1 as is, separate transcripts skip it, and the player plays Track 1 alone when every track is on (or just the separate tracks you left on), so nothing doubles up
- **Silent tracks are skipped:** tracks with no sound at all (an empty OBS track) are found automatically and left out of the tracks window, the mix and the player
- **Pick which audio tracks to hear:** right-click the volume button in the player to turn each track on or off. Tracks that weren't transcribed start muted. Rows from a muted track are grayed out, and the blue now-playing row and the captions skip them
- **Fullscreen video:** hover over the preview and click the button in its top right corner (or double-click the video). Esc, F11 or the same button comes back. Captions show the speaker's name in their color
- **Split a line (Ctrl+X):** splits the highlighted row at its first period, or, while editing, where the text cursor is. The second half starts a second later. **Ctrl+click** a row to add a blank row under it. Both save right away; Ctrl+Z undoes them
- **Space bar** plays and pauses the preview (not while you're editing a row or typing in a box)
- **Keyboard:** click in the transcript, then Up/Down (or Page Up/Page Down) move the highlighted row and Enter plays from it
- **Find (Ctrl+F):** a find box in the top right of the transcript highlights every match and dims the rest, with a count and up/down arrows (Enter / Shift+Enter) to jump between them
- **faster-whisper engine:** install it (see Setup) and transcription runs about 4x faster with the same models. Pick the engine in Settings (Automatic uses faster-whisper when it's installed)
- **Word timing:** every word's time is saved. Click a word to play from it, and splitting a line (Ctrl+X) uses the exact time the next word was said
- **Find and replace (Ctrl+H):** opens a replace row in the find box. Only the spoken text is changed (not names or times), and replacements wait for Save like other edits
- **Search every transcript:** the search box in the Transcripts tab finds what was said, not just file names. Matching lines show on the right; click one to open that transcript at that line
- **Playback speed:** the 1x button in the player (0.5x to 2x)
- **Talk time:** the chart button above a transcript shows how long each person spoke
- **Backups:** each time a change is saved, the previous copy is kept (the last 20 per transcript). The History button above a transcript goes back to any of them
- **Call view:** audio-only recordings with detected speakers show each person as a tile in the player, like a video call. Whoever is talking lights up and the rest gray out
- **Combine recordings made at the same time:** a lav mic per person, a camera, isolated game captures. Select the files in the Queue, right-click > **Combine as one recording**, and pick who each file is. The files are lined up automatically by matching their sound (or by their recording times), lines a mic picked up from someone else are removed, and everyone's lines show together, named, with the video (if any) as the picture
- **Focus on someone:** right-click a row > **Focus on [name]**. Their own mic or track plays loud and everyone else quietly; on a shared track, the volume drops whenever someone else is talking. Click the ◎ chip in the player to stop
- **Markers:** press **M** on a row (or right-click > Mark) for a thin colored line on its right edge. Markers are saved in `<name>_markers.json`, so Claude can read a transcript and mark good moments too (in orange, with a note you see when hovering the row)
- **Add another recording:** right-click a saved transcript > Add another recording, to bring in other people's mics or POVs later. Your transcript is reused as it is (only the new files are transcribed), everything stays on its timeline so markers keep their place, and lines are labeled by person
- **Resize the panels:** drag the divider between the list and the transcript; double-click it to go back to automatic
- **Updates:** once a day the app checks GitHub for a new version (Settings > Updates, on by default) and shows an Update button by the status text. The installed apps download and install it, then reopen; the Python version runs `git pull` (or use GitHub Desktop). Nothing installs without you clicking
- **What's installed** (Settings): every piece the app uses with a check or cross, what it's for, and how to add what's missing: a command to copy, or an **Install** button that does it for you with progress (pip packages in the Python version, the speaker detection add-on in the installed app). Features that need something missing offer to open it
- **Clip sound:** when exporting a clip from a combined recording or a multi-track file, pick which mics or tracks go in (it starts with what you're hearing in the player); they're mixed and lined up
- **Working indicator:** long jobs (clip export, redoing lines, detecting speakers) run in the background with a small progress bar by the status text, so the window never freezes
- **One master list of lines:** every version keeps its lines in one file (`_speakers.json`, even without speakers); edits go there and the .txt/.srt/.vtt are rebuilt from it, so they always match. Older versions get theirs the first time you open them
- **Made-up text:** words Whisper wrote with no sound under them are crossed out in gray. Right-click the row > Remove made-up words (or remove them from every line at once), then Save
- **Mark exact words:** select words (or several rows) and press **M** for a marker covering just that stretch, tinted in the text; editors get it as a marker with a length
- **Line up times with the voice:** right-click a saved transcript > Line up times with the voice moves every line's start and end to where the talking really starts and stops (Whisper often starts a line up to a second early). Earlier copies keep the old times
- **Export clip:** right-click a row > Export clip saves that moment (a range marker's exact stretch, or the selected rows) as an mp4 with optional burned-in captions, or an mp3 for audio files
- **Redo lines:** select rows, right-click > Redo these lines with > a model, to transcribe just that part again. Saved as a new version
- **Word timings for every version:** a version without them (like a Claude version) borrows them from another version of the same audio, and edited lines keep the times of the words still in them
- **Words to know:** names and terms in Settings > Words to know, plus "Words they use" for each person (People > edit), help Whisper spell them right. Everyone's name is included automatically
- **DaVinci Resolve, everything at once:** export button > "DaVinci Resolve: audio + markers + captions" saves a timeline (.fcpxml) with the original audio or video and the markers on it, plus the captions (.srt; with speaker names when more than one person talks, and timed to where the voice really starts and stops in the audio, since Whisper's times can be a second off). In Resolve: File > Import > Timeline (the .fcpxml); then in the Media Pool right-click that timeline > Timelines > Import > Timeline Markers from EDL (the _markers.edl); then File > Import > Subtitle (the .srt) and drag it onto the timeline
- **Export markers:** the export button above the transcript saves them for DaVinci Resolve (.edl; in the Media Pool right-click the timeline > Timelines > Import > Timeline Markers from EDL), Final Cut Pro (.fcpxml) or Premiere Pro (.xml, File > Import). Final Cut and Premiere get the original file as a clip with the markers on it
- **Caption styles** (Settings > Captions): below the video, or on the video like a movie, with a see-through box, solid box, outline or shadow; size, color, top or bottom, and speaker names on or off
- **Audio only preview:** right-click the video > Audio only shows the speakers as call tiles instead of the picture
- On a wide window, the transcript stops at a comfortable width and the video gets the extra room
- Uses your NVIDIA GPU automatically when available (much faster)
- Light and dark mode that follows your Windows setting, switching live when you change it (or pick Light or Dark in Settings)
- A loading screen at launch loads Whisper and your model first, so the app opens ready to go. A first-time model download shows its progress.

## Install

**The easy way (no Python needed):** download the latest build from the repo's **Releases** page (or the **Actions** tab > Build apps > the newest run > Artifacts):

- **Windows:** `Whisper-Transcriber-Setup-x.y.z.exe`. Run it; it installs for your user (no admin) with a Start menu shortcut. ffmpeg is included.
- **Mac:** `Whisper-Transcriber-Mac-apple-silicon.zip` (M1 and newer) or `-intel.zip`. Unzip and drag the app to Applications. The first time, **right-click it > Open** (it isn't signed with a paid Apple developer account, so a plain double-click is blocked once). Install ffmpeg with [Homebrew](https://brew.sh): `brew install ffmpeg`.
- **Both:** for the video preview, install [VLC media player](https://www.videolan.org/vlc/).

The installed app transcribes with faster-whisper. **Speaker detection is an optional add-on** (it needs PyTorch, which is big): Settings > What's installed > Install, or tick "Detect who's speaking" and it offers to. It downloads once into the app's settings folder: about 400 MB (CPU version), or about 3 GB for the GPU version when an NVIDIA card is found (you pick). Remove that folder (`addons` in `%APPDATA%\WhisperTranscriber` or `~/Library/Application Support/WhisperTranscriber`) to uninstall it.

**Releasing an update:** bump `__version__` in `transcriber/__init__.py`, commit, and push a tag with the same number (like `v2.3.0`). GitHub builds the installers and publishes the release, and everyone's app offers the update the next day.

**To build the installers yourself:** push a tag like `v2.2.0` (or run the Build apps workflow by hand in the Actions tab). GitHub builds the Windows installer and both Mac apps and attaches them to a release. Locally: `pip install -r packaging/requirements-app.txt pyinstaller`, `python packaging/make_icon.py`, `pyinstaller packaging/whisper_transcriber.spec`.

## Setup (Python version, every feature)

**Mac:** install [Homebrew](https://brew.sh), then in Terminal, in this folder: `bash mac_setup.sh`. It installs Python, ffmpeg and VLC and sets everything up; then double-click **Whisper Transcriber.command**. Optional speaker detection: `.venv/bin/python -m pip install pyannote.audio` (then the Hugging Face steps in 5 below). On a Mac, right-click is a two-finger click, Cmd works for the shortcuts (Cmd+Z, Cmd+S, Cmd+F), and transcription runs on the CPU (Apple Silicon is quick with faster-whisper).

**Windows:**

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
4. **Optional, video preview**: install [VLC media player](https://www.videolan.org/vlc/) (64-bit). `python-vlc` from requirements.txt connects to it. Without VLC the app works the same, minus the player.
5. **Optional, speaker detection**: run `pip install pyannote.audio`. Then make a free account at [huggingface.co](https://huggingface.co), accept the terms on the [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) page, create a **Read** token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens), and paste it into Settings > Speakers. To record voice samples in People, also run `pip install soundcard`. The model downloads once, then runs offline. Your token is stored only on your PC.
6. **Optional, faster transcription**: run `py -3.12 -m pip install faster-whisper`. It uses your GPU when PyTorch's CUDA build is installed (step 7) and falls back to the CPU otherwise. If it ever causes problems, set Settings > Engine to OpenAI Whisper.
7. **Optional, for NVIDIA GPUs**: install the CUDA build of PyTorch for a big speedup. Pick your options at [pytorch.org](https://pytorch.org/get-started/locally/) and run the command it gives you.

## Use it

**Desktop app:** double-click `Transcriber.pyw` (opens without a console window). A loading screen appears while Whisper and your selected model load (press **Skip** to open right away and load the model on your first transcription). Add files in the **Queue** tab and click **Transcribe**. Click any file to see its transcript on the right; double-click a finished one to open the file. The **Transcripts** tab shows everything saved so far. Model, language, formats, folder and theme are under **Settings**.

**Quick transcribe, without the app:** drop files onto `Quick Transcribe.bat`. A console window shows progress; it uses the medium model and saves to `Documents\Transcripts`.

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

Each recording gets its own folder inside the transcripts folder (`Transcripts/<name>/`), holding its transcripts, versions, tracks and markers. Loose files from older versions of the app are moved into folders automatically.

If a transcript with the same name already exists, the new one is saved as `name (2)` instead of overwriting it.

## Project layout

```
app.py                 The main window: layout, theme, the transcript panel, background events
transcribe_video.py    Command-line tool
transcriber/core.py    Reading audio (ffmpeg) and transcribing (faster-whisper / OpenAI Whisper)
transcriber/names.py   File names of saved transcripts, in one place
transcriber/files.py   Saved transcripts: finding, grouping, reading and writing them
transcriber/words.py   Word timings, made-up text, borrowing word times
transcriber/edits.py   Changing saved transcripts (edits, splits, adding speakers later)
transcriber/queue_ui.py   The Queue tab and transcribing in the background
transcriber/saved_ui.py   The Transcripts tab: list, versions, showing, deleting
transcriber/loading_ui.py The loading screen
transcriber/player.py  VLC preview player with captions
transcriber/editing.py Editing transcript lines in place
transcriber/finder.py  Ctrl+F find box
transcriber/markers_ui.py, marker_export.py  Markers and exporting them to video editors
transcriber/review_ui.py  Made-up text, redo lines, export clip, lining times up
transcriber/timing.py     Lining line times up with the voice in the audio
transcriber/session.py, session_ui.py  Combined recordings
transcriber/speakers.py, diarize_worker.py  Speaker detection and the speakers files
transcriber/people.py, people_ui.py, cropper.py, recorder.py  People, photos, voice samples
transcriber/tracks_ui.py  Audio tracks
transcriber/search_ui.py  Searching every transcript
transcriber/tools_ui.py   Talk time, backups, adding speakers later
transcriber/backups.py    Copies kept before each save
transcriber/settings_ui.py  Settings window
transcriber/setup_check.py, setup_ui.py, addons.py  What's installed, and installing pieces
transcriber/updater.py, update_ui.py  Checking for and installing updates
transcriber/fonts.py, icons.py, ui_common.py, optional.py  Look and small shared helpers
Transcriber.pyw        Opens the app (no console window)
Quick Transcribe.bat   Drag-and-drop transcription without the app
mac_setup.sh           Sets up the Python version on a Mac
packaging/             Building the Windows installer and Mac app (PyInstaller, Inno Setup)
```

## Development

Tests for the file handling (editing, splitting, word timings, versions, silent tracks, backups) are in `tests/`. Run them with `py -3.12 -m pip install pytest` and then `py -3.12 -m pytest`. GitHub runs them on every push (see the Actions tab).

### Markers file (for adding markers from outside the app)

`<transcript name>_markers.json` next to the transcript:

```json
{"version": 1, "markers": [{"time": 83.4, "track": "Mic", "text": "the line", "by": "claude", "note": "why"}]}
```

`time` is the row's start in seconds; `track` and `text` are optional and help pick the right row. `by` is `"you"` (blue) or `"claude"` (orange). The app picks up changes within a few seconds.
