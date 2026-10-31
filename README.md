# Whisper Transcriber

A desktop app for Windows and Mac that turns video and audio files into timestamped transcripts using [OpenAI Whisper](https://github.com/openai/whisper). Everything runs locally on your computer; nothing is uploaded.

- Drag in videos or audio (MP4, MKV, MOV, MP3, WAV, M4A and more), or a whole folder
- Queue several files and watch the transcript appear line by line
- Each file in the list shows a preview frame (videos) or an audio icon (audio files), plus its length
- A Library tab lists everything in your transcripts folder and opens any transcript when you click it; Ctrl+F there searches what was said in all of them
- Layout: the video player (and timeline) fill the left side; the right side has three tabs: **Transcript** (what's open), **Queue** (files to transcribe; shows a count like Queue (2) while busy) and **Library** (everything saved). Clicking a file in Queue or Library opens it in Transcript; Ctrl+1, Ctrl+2 and Ctrl+3 switch tabs
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
- **Record a voice sample:** the microphone button next to each person records them talking, from your microphone or from PC audio (a friend on Discord, a video). Sentences to read are shown if you want them, or just talk. Needs speaker detection set up, plus `pip install soundcard`. After Stop you can play it back, then Save it or Discard it. A recording with no sound or hardly any talking isn't used
- **Edit voice samples:** People > Samples lists each one (where it came from, when, how much talking), plays the ones saved from now on, and removes any you pick. Samples that don't sound like the person's others show in red. Naming a speaker, then renaming them, moves the sample to the right person instead of keeping it under both
- **Change a line's time:** double-click its timestamp, type the time (like 1:23 or 0:01:23), Enter. It saves with the other changes, and lines are put back in time order
- **Fix wording in place:** double-click any row to edit its text right there (Enter or Esc to finish). Typing is instant; the timeline and the video's captions catch up as soon as you pause. Changed letters get a yellow highlight, and a bar appears under the transcript with Discard, Undo, Redo and Save (Ctrl+Z, Ctrl+Y, Ctrl+S work too). Saving updates the .txt/.srt/.vtt files
- **Right-click a row** for a menu: Edit text, Change Speaker (fixes one line without renaming that speaker everywhere), Copy text, and Delete line. Deleted lines turn red and are removed when you click Save in the bar under the transcript (Undo and Discard work for them too)
- **OBS-style Track 1:** tick "Track 1 already has everything" in the tracks window (remembered). Then "mixed" uses Track 1 as is, separate transcripts skip it, and the player plays Track 1 alone when every track is on (or just the separate tracks you left on), so nothing doubles up
- **Silent tracks are skipped:** tracks with no sound at all (an empty OBS track) are found automatically and left out of the tracks window, the mix and the player
- **Pick which audio tracks to hear:** right-click the volume button in the player to turn each track on or off. Tracks that weren't transcribed start muted. Rows from a muted track are grayed out, and the blue now-playing row and the captions skip them
- **Fullscreen video:** hover over the preview and click the button in its top right corner (or double-click the video). Esc, F11 or the same button comes back. Captions show the speaker's name in their color
- **Split a line (Ctrl+X):** splits the highlighted row at its first period, or, while editing, where the text cursor is. The second half starts a second later. **Ctrl+click** a row to add a blank row under it. Both save right away; Ctrl+Z undoes them
- **Milliseconds:** right-click the transcript > Timestamps with milliseconds shows times like [00:01:23.456]. Double-clicking a time to change it always shows and keeps the milliseconds, so lines can start at 26.4 s instead of 26
- **Space bar** plays and pauses the preview (not while you're editing a row or typing in a box), even right after clicking a button
- **Extract speech first** (Settings > Transcription): before Whisper hears a track, Demucs pulls the voice out of it and leaves game sound, music and room noise behind, for cleaner words from noisy recordings. It separates voice from everything else, not one person from another (another person caught by the same mic is still there; that's what Remove crosstalk is for). `pip install demucs` (What's installed > Extract speech); slow without a GPU, and the first run downloads the model (about 80 MB)
- **Record to transcribe:** Queue tab > Record... records your microphone, PC audio (whatever is playing: a stream, a call, a video you can't download), or both, then transcribes it when you press Stop. With both, each is its own track, so your side and the other side are transcribed separately and labeled (Mic, PC audio). Level meters show each source is coming through; a source with no sound is left out. The recording is saved in your transcripts folder under Recordings (.m4a), so it plays back, shows on the timeline and can be redone like any file. **One app** records just that app's sound (a browser, Discord, a game) instead of everything the PC plays, so notifications and music elsewhere stay out; pick its window in the list (↻ refreshes it). A browser is recorded as a whole, all its tabs. Windows 10 version 2004 or newer, and `pip install proc-tap` (What's installed > Recording one app). The window stays on top while you play the video elsewhere; Discard throws a recording away. (Needs `pip install soundcard`, like voice samples.)
- **Save video with captions:** right-click a transcript in the list (or the video, or the export button) > Save video with captions. Saves a copy of the video with the captions inside it as a subtitle track, so VLC, Plex, smart TVs and phones list them under Subtitles / CC and can turn them on or off (they show by default). Nothing is re-encoded, so it's quick and the picture and sound are untouched; every audio track is kept. MKV and other videos become an .mkv (SRT track), MP4 stays .mp4 and MOV stays .mov (mov_text track). The original file is never changed
- **Edit from the video:** double-click a caption on the video (or in the strip below it) and type on it right there: a box opens where the caption sits with the line's words in it; Enter keeps them, Esc leaves them as they were. The change goes to that transcript line as a pending edit like any other (yellow highlight, Save / Undo). Unsaved edits show in the captions right away
- **Keyboard:** click in the transcript, then Up/Down (or Page Up/Page Down) move the highlighted row and Enter plays from it
- **Find (Ctrl+F):** a find box in the top right of the transcript highlights every match and dims the rest, with a count and up/down arrows (Enter / Shift+Enter) to jump between them
- **faster-whisper engine:** install it (see Setup) and transcription runs about 4x faster with the same models. Pick the engine in Settings (Automatic uses faster-whisper when it's installed)
- **Word timing:** every word's time is saved. Click a word to play from it, and splitting a line (Ctrl+X) uses the exact time the next word was said
- **Find and replace (Ctrl+H):** opens a replace row in the find box. Only the spoken text is changed (not names or times), and replacements wait for Save like other edits
- **Search every transcript:** Ctrl+F on the Library tab finds what was said, not just file names: the list narrows to the transcripts that say it (↑/↓ move through them), Enter shows every matching line (click one to open that transcript at that line), and opening a transcript keeps the words highlighted in it. Esc shows the whole list again. On the Transcript tab, Ctrl+F finds in that transcript
- **Playback speed:** the 1x button in the player (0.5x to 2x)
- **Talk time:** the chart button above a transcript shows how long each person spoke
- **Backups:** each time a change is saved, the previous copy is kept (the last 20 per transcript). The History button above a transcript goes back to any of them
- **Call view:** audio-only recordings with detected speakers show each person as a tile in the player, like a video call. Whoever is talking lights up and the rest gray out
- **Combine recordings made at the same time:** a lav mic per person, a camera, isolated game captures. Select the files in the Queue, right-click > **Combine as one recording**, and pick who each file is. The files are lined up automatically by matching their sound (or by their recording times), lines a mic picked up from someone else are removed (when two mics caught the same words at the same moment, only the closest mic's line stays, judged against each mic's own usual level so a mic set louder doesn't win; a line only one mic has is always kept), and everyone's lines show together, named, with the video (if any) as the picture
- **Focus on someone:** right-click a row > **Focus on [name]**. Their own mic or track plays loud and everyone else quietly; on a shared track, the volume drops whenever someone else is talking. Click the ◎ chip in the player to stop
- **Markers:** press **M** on a row (or right-click > Mark) for a thin colored line on its right edge. Markers are saved in `<name>_markers.json`, so Claude can read a transcript and mark good moments too (in orange, with a note you see when hovering the row)
- **Remove crosstalk later:** right-click a combined recording in the Library > Remove crosstalk, to clean up one made before this got better. The copies are marked as deleted (struck through) but nothing changes until you Save, so you can look them over first or Undo. It reads each mic's audio, so the original files need to be reachable (a NAS has to be connected)
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
- **Export clip:** right-click a row > Export clip (or mark a stretch with the timeline's Range tool > Clip) saves that moment (a range marker's exact stretch, the selected rows, or the marked stretch) as an mp4, or an mp3 for audio files. Captions are burned in by default and also go in as a subtitle track (VLC, TVs, YouTube and editors list it under Subtitles, on from the start; times count from the clip's start). Microsoft's Media Player app ignores subtitle tracks inside mp4 files, so burned-in is the only kind it shows. Pick Burned in, Subtitle track only, or None if you'd rather; the choice is remembered. **Highlight each word as it's said** lights up the word being spoken in the burned-in captions (the speaker's color, or yellow), the way short-form videos do
- **Cut a video from the transcript:** export button > Cut video. Keep only the marked parts (every marker: a marked stretch, or the line it's on; mark with M or Mark selection), or leave out the deleted lines (the ones struck out and not yet saved). A little room is kept around each part, the parts are exported and joined, and the captions go along with the gaps taken out of their times (burned in, as a track, both or none, like a clip). A highlight reel from a long session without opening an editor
- **Check captions:** export button > Check captions (or right-click a transcript in the Library) lists what a viewer would trip over: captions reading too fast (over 20 characters a second), on screen too briefly or too long, overlapping in the same spot, lines too long, and transcript lines where Whisper got stuck repeating itself. Double-click one to go to it. Unsaved edits count, so fix, check again, then save
- **Redo lines:** select rows, right-click > Redo these lines with > a model, to transcribe just that part again. Saved as a new version
- **Word timings for every version:** a version without them (like a Claude version) borrows them from another version of the same audio, and edited lines keep the times of the words still in them
- **Words to know:** names and terms in Settings > Words to know, plus "Words they use" for each person (People > edit), help Whisper spell them right. Everyone's name is included automatically
- **DaVinci Resolve, everything at once:** export button > "DaVinci Resolve: audio + markers + captions" saves a timeline (.fcpxml) with the original audio or video and the markers on it, plus the captions (.srt; with speaker names when more than one person talks, and timed to where the voice really starts and stops in the audio, since Whisper's times can be a second off). In Resolve: File > Import > Timeline (the .fcpxml); then in the Media Pool right-click that timeline > Timelines > Import > Timeline Markers from EDL (the _markers.edl); then File > Import > Subtitle (the .srt) and drag it onto the timeline
- **Send captions straight into DaVinci Resolve:** export button > "DaVinci Resolve: send captions to the open timeline". With Resolve open on the timeline (Edit page) and Preferences > System > General > External scripting using set to Local, the captions land on it: as **Text+ titles**, one per caption on a new video track, each in its person's color (needs a Text+ in Resolve's media pool: drag Text+ from Effects > Titles into the media pool once and style it the way the captions should look; captions stacked above another person's go on a second track), or as a **subtitle track** (Resolve's own captions, one style for all, no setup). Uses Resolve's scripting module, found automatically where Resolve installs it
- **Export markers:** the export button above the transcript saves them for DaVinci Resolve (.edl; in the Media Pool right-click the timeline > Timelines > Import > Timeline Markers from EDL), Final Cut Pro (.fcpxml) or Premiere Pro (.xml, File > Import). Final Cut and Premiere get the original file as a clip with the markers on it
- **Subtitle timeline** (Timeline button at the top): a strip under the video player like a video editor's; the video shrinks to make room and grows back when it's closed. One lane per track (or person), each with its audio waveform and its lines as blocks in the speaker's color. Drag a block to move it, or drag its edges to change when it starts and ends; edges snap to where the voice starts and stops, to the neighbouring lines and to the playhead (hold Alt to place freely, or turn Snap off). Lines can't overlap within a lane (within the same caption spot; one dragged on top of another, like a pasted copy stacked over the original, can always be dragged off again). The red playhead follows the player; click anywhere to jump there, double-click a block to edit its text, Left/Right nudge the selected line (Shift for bigger steps). Scroll (or hold the scroll wheel and drag) to move along, Ctrl+scroll to zoom, Fit shows everything. Changes are edits like any other: the transcript and the player's captions update right away, and Save / Discard / Undo work as usual. Drag the panel's top edge to resize it. **Delete** removes the selected line, **Ctrl+C / Ctrl+V** copy it to the playhead (on its track, fitted into the gap), and **right-click** a line for a short menu (edit, change speaker, mark, delete; Export clip when it's inside a marked stretch); Ctrl+Z undoes. Change Speaker lists the people in this transcript first, with their pictures, and everyone else under Other. A line you stretch or move on the timeline keeps exactly that time in the captions (on the video, in caption files and exports), instead of following when each word was said. A faint tail after a line shows how much longer its caption stays on screen (captions are kept up long enough to be read; hover for the time); drag the line's edge if you want the end exact. Dragging the playhead scrubs the video smoothly, captions and all. **Tools:** Select (S) moves and resizes lines; Snip (B) splits a line in two where you click, between words (a dashed line shows where; Ctrl+Z undoes it); Range (R) marks a stretch (drag over it): ▶ (or Shift+Space) plays just that part in the player as a preview of the export, captions and all, and Clip exports it as its own video or audio file (the Export clip window, with the times filled in); right-click the stretch for the same; Esc or × clears it. Change the keys in Settings > Timeline
- **Paragraph view:** the ¶ button above the transcript groups lines into paragraphs (same speaker, no long pause). Each shows its start time with the end time under it; photos sit in one column and wrapped text lines up where the words start. Click ▸ or double-click a paragraph to open it into its lines, ▾ to fold it again; right-click for Open all / Fold all. Clicking plays from the paragraph's start, and searching, markers and captions work as usual
- **Captions done the broadcast way** (Settings > Captions > Caption-sized pieces, on by default): long lines are cut into captions of at most 2 lines of 42 characters and 7 seconds, broken at natural places (after punctuation, before "and", "to"...; never between "the" and its word or a first and last name), timed by the words. Captions stay up at least 5/6 of a second and long enough to read, gaps under half a second are closed so they don't flicker, and the speaker's name appears only when the speaker changes. Used in the player, .srt/.vtt files, the DaVinci Resolve export and burned-in clips
- **People talking at once** (Settings > Captions > Talking at once): each person gets their own caption, stacked one above the other (up to three; whoever started first keeps the bottom spot, and a fourth waits for a free one), so nobody's line is cut short. Pick "One caption with dashes" to have them share a caption with a dash each instead. On the timeline, drag a line up within its lane to stack it by hand (the lane shows three rows while you drag; the usual spot is the bottom one), and drag it back down to undo; the spot you pick is kept and other captions go around it. Stacked captions show in the player, .vtt (line position), .ass and burned-in clips; .srt has no positions, so there the overlaps fold into one caption with dashes
- **Speaker colors on captions** (Settings > Captions > Speaker color): just the name in the person's color, all the text in their color, or a box in their color behind the words. Works below the video, on the video, and in burned-in clips (which now use your caption look too). **Colors...** (in Settings > Captions, or People) sets each person's color
- **Scene cuts:** in exports from a video, captions start and end on a nearby cut (stretches up to 30 minutes)
- **Sounds:** Settings > Captions > Write sounds like [laughs] lets Whisper note sounds such as [laughs] or [music] in new transcripts (it catches some, not all). Sound notes are written the standard way, in brackets
- **Caption styles** (Settings > Captions): below the video, or on the video like a movie, with a see-through box, solid box, outline or shadow; size from 50% to 400% (also in the video's right-click menu > Caption size), color, top or bottom, and speaker names on or off
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

The **NVIDIA models** (Parakeet and Nemotron) use the speaker detection add-on's Python: install that first, then Settings > What's installed > NVIDIA models > Install (adds Transformers to it).

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
8. **Optional, NVIDIA Parakeet and Nemotron**: Settings > What's installed > NVIDIA models > Install. It updates Transformers (5.21 or newer) and, with an NVIDIA card and the CPU version of PyTorch, swaps in PyTorch's GPU version: the app closes, a window shows the install (about 3 GB), and the app opens again. By hand: `pip install "transformers>=5.21"` and `pip install --upgrade --force-reinstall --no-deps torch --index-url https://download.pytorch.org/whl/cu126`.

## Use it

**Desktop app:** double-click `Transcriber.pyw` (opens without a console window). A loading screen appears while Whisper and your selected model load (press **Skip** to open right away and load the model on your first transcription). Add files in the **Queue** tab and click **Transcribe**. Click any file to see its transcript on the right; double-click a finished one to open the file. The **Library** tab shows everything saved so far. Model, language, formats, folder and theme are under **Settings**.

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
| parakeet | Fastest on a GPU | Very good | English and 24 European languages ([NVIDIA Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)) |

Each model downloads once (75 MB for tiny up to about 3 GB for large) and is reused after that.

**parakeet** is NVIDIA's model, not Whisper: pick it in the model menu like the others (it needs the NVIDIA models from What's installed; about 2.5 GB to download the first time). It's a little more accurate than Whisper large on the standard tests, much faster on an NVIDIA card, and doesn't make up words in silence. It knows English and 24 European languages (Spanish, French, German, Italian, Portuguese, Russian, Ukrainian, Polish, Dutch and others) and tells them apart by itself; for other languages use a Whisper model. It doesn't take the Words to know list.

**Speaker model** (Settings > Speakers): pyannote (the default, any number of people) or [NVIDIA Nemotron 3 Diarization](https://huggingface.co/nvidia/Nemotron-3-Diarization) (newer, notices people talking over each other, up to 8 people; best with an NVIDIA card). With either, voiceprints come from pyannote's voice model, so the people you've taught are recognized the same way (pyannote still needs to be set up). If Nemotron can't run, the app uses pyannote for that transcript and says so. Voice samples in People always use pyannote.

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

## Crash and error logs

If the app crashes or something goes wrong, the details are saved on your computer (nothing is sent anywhere): Settings > Error logs... opens the folder (`%APPDATA%\WhisperTranscriber\logs` on Windows, `~/Library/Application Support/WhisperTranscriber/logs` on a Mac).

- `errors.log`: every error, with the traceback and what was on screen
- `crash-<date>.log`: the report from a run that closed unexpectedly (also crashes inside VLC or other libraries)
- `last_run.log`: the current run's output

After a crash, the next start says so in the status bar.

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
transcriber/saved_ui.py   The Library tab: list, versions, showing, deleting
transcriber/loading_ui.py The loading screen
transcriber/player.py  VLC preview player with captions
transcriber/editing.py Editing transcript lines in place
transcriber/finder.py  Ctrl+F find box
transcriber/markers_ui.py, marker_export.py  Markers and exporting them to video editors
transcriber/review_ui.py  Made-up text, redo lines, export clip, cut video, lining times up
transcriber/cuts.py       Cutting a video from the transcript: which spans to keep, captions moved onto the cut timeline
transcriber/checks.py, checks_ui.py  Caption quality checks (reading speed, timing, loops) and their window
transcriber/resolve_bridge.py  Captions straight into a running DaVinci Resolve (Text+ titles or a subtitle track)
transcriber/speech.py     Extract speech before transcribing (Demucs)
transcriber/timing.py     Lining line times up with the voice in the audio, scene cuts
transcriber/captions.py   Turning lines into captions (sizes, line breaks, timing rules, styled ASS)
transcriber/timeline.py, timeline_ui.py  The subtitle timeline (the widget, and how it connects to the app)
transcriber/subtitle_track.py  A copy of a video with the captions as a subtitle track (ffmpeg, no re-encoding)
transcriber/session.py, session_ui.py  Combined recordings
transcriber/speakers.py, diarize_worker.py  Speaker detection and the speakers files
transcriber/nvidia_worker.py  NVIDIA Parakeet (speech to text) and Nemotron (who's speaking), through Transformers
transcriber/record_ui.py      Recording the mic, PC audio or one app to transcribe (recorder.py records, app_audio.py finds the apps)
transcriber/people.py, people_ui.py, cropper.py, recorder.py  People, photos, voice samples
transcriber/tracks_ui.py  Audio tracks
transcriber/search_ui.py  Searching every transcript
transcriber/tools_ui.py   Talk time, backups, adding speakers later
transcriber/backups.py    Copies kept before each save
transcriber/settings_ui.py  Settings window
transcriber/setup_check.py, setup_ui.py, addons.py  What's installed, and installing pieces
transcriber/updater.py, update_ui.py  Checking for and installing updates
transcriber/crashlog.py   Crash and error logs
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
