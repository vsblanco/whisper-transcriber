"""Tests for the file handling behind the app: editing, splitting, word timings, versions,
silent tracks and backups. Run with:  python -m pytest"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcriber import core, edits, files, words  # noqa: E402,F401
from transcriber.core import Segment, Transcript
from transcriber.words import read_words, word_at
from transcriber.files import group_versions, save_transcript, scan_transcripts
from transcriber.edits import apply_text_edits, split_line


def make(tmp_path, speakers=False, words=True, formats=("txt", "srt")):
    segs = [Segment(0.0, 1.5, "Hello there. How are you", [(0.0, 0.3, " Hello"), (0.3, 0.6, " there."),
                                                         (0.8, 1.0, " How"), (1.0, 1.2, " are"), (1.2, 1.5, " you")]),
            Segment(2.0, 3.5, "Second line.", [(2.0, 2.6, " Second"), (2.6, 3.5, " line.")]),
            Segment(4.0, 5.0, "Third line.", [(4.0, 4.5, " Third"), (4.5, 5.0, " line.")])]
    if not words:
        for s in segs:
            s.words = []
    t = Transcript(source=str(tmp_path / "talk.mp4"), model="tiny", language="en", segments=segs,
                   text=" ".join(s.text for s in segs))
    if speakers:
        t.speakers, t.names, t.voiceprints, t.people_ids = ["A", "B", "A"], {"A": "Speaker 1", "B": "Speaker 2"}, {}, {}
    saved = save_transcript(t, tmp_path, list(formats))
    files = {}
    for p in saved:
        n = p.name
        files["speakers" if n.endswith("_speakers.json") else "words" if n.endswith("_words.json")
              else "txt" if n.endswith(".txt") else n[-3:]] = p
    return files


def txt_lines(files):
    return [ln for ln in Path(files["txt"]).read_text(encoding="utf-8").splitlines() if ln.startswith("[")]


class People:
    people = []

    def get(self, pid):
        return None


def test_edit_delete_and_time(tmp_path):
    f = make(tmp_path)
    apply_text_edits(f, {1: "SECOND!"}, deletes=[2], times={0: 7})
    assert txt_lines(f) == ["[00:00:02] SECOND!", "[00:00:07] Hello there. How are you"]
    srt = Path(f["srt"]).read_text(encoding="utf-8")
    assert srt.startswith("1\n00:00:02,000 --> 00:00:03,500\nSECOND!")
    assert "2\n00:00:07,000 --> 00:00:08,500\nHello there. How are you" in srt
    words = read_words(f["words"])
    assert [len(w) for w in words] == [1, 5]          # the edited line keeps the times of its remaining words
    assert words[1][0][0] == pytest.approx(7.0)       # the moved line's words moved with it


def test_edits_with_speakers_file(tmp_path):
    f = make(tmp_path, speakers=True)
    apply_text_edits(f, {0: "Hi."}, People())
    data = json.loads(Path(f["speakers"]).read_text(encoding="utf-8"))
    assert data["segments"][0]["text"] == "Hi."
    assert txt_lines(f)[0] == "[00:00:00] Speaker 1: Hi."


def test_split_keeps_order_and_words(tmp_path):
    f = make(tmp_path)
    split_line(f, 0, "Hello there.", "How are you", 0.0, 0.8)
    assert txt_lines(f)[:2] == ["[00:00:00] Hello there.", "[00:00:00] How are you"]
    words = read_words(f["words"])
    assert [w[2] for w in words[0]] == [" Hello", " there."]
    assert words[1][0][0] == pytest.approx(0.8)
    assert len(words) == 4


def test_word_at():
    ws = [[0.0, 0.3, " Hello"], [0.3, 0.6, " there."], [0.8, 1.0, " How"]]
    assert word_at(ws, "Hello there. How", 8)[2] == " there."
    assert word_at(ws, "Hello there. How", 14)[2] == " How"
    assert word_at(ws, "Goodbye", 2) is None


def test_versions_and_tracks_group(tmp_path):
    make(tmp_path)
    make(tmp_path)                                    # a second version: "talk (2)"
    t = Transcript(source=str(tmp_path / "talk.mp4"), model="tiny", language="en",
                   segments=[Segment(0, 1, "mic")], text="mic", track="Mic")
    save_transcript(t, tmp_path, ["txt"])
    groups = group_versions(scan_transcripts(tmp_path))
    assert len(groups) == 1
    g = groups[0]
    assert sorted(g["tracks"]) == ["", "Mic"]
    assert len(g["tracks"][""]) == 2
    assert "words" in g["tracks"][""][0]["files"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_silent_track_found(tmp_path):
    out = tmp_path / "three.mkv"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=3",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-f", "lavfi", "-i", "sine=f=300:d=3",
                    "-map", "0:a", "-map", "1:a", "-map", "2:a", "-t", "3", "-c:a", "aac", str(out)], check=True)
    assert core.silent_tracks(str(out)) == {1}


def test_match_people_threshold():
    from transcriber.speakers import match_people

    class P:
        people = [{"id": "p1", "name": "Mel", "voices": [[0.0, 1.0, 0.1]]}]

        def get(self, pid):
            return self.people[0] if pid == "p1" else None

    ids, names = match_people(["S0", "S1"], {"S0": [1.0, 0.0, 0.1], "S1": [0.05, 0.98, 0.1]}, P())
    assert ids == {"S1": "p1"}
    assert names == {"S0": "Speaker 1", "S1": "Mel"}


def test_backup_and_restore(tmp_path, monkeypatch):
    from transcriber import backups
    monkeypatch.setattr(backups, "BACKUP_DIR", tmp_path / "backups")
    folder = tmp_path / "t"
    folder.mkdir()
    f = make(folder)
    backups.backup(f, "20260101-120000")
    apply_text_edits(f, {0: "changed"})
    found = backups.list_backups([f])
    assert found[0][0] == "20260101-120000"
    backups.restore(found[0][1])
    assert txt_lines(f)[0] == "[00:00:00] Hello there. How are you"
    assert len(backups.list_backups([f])) == 2       # the version before the restore was kept too


def _tone_file(tmp_path, name, parts, start=0.0, bleed=None, dur=30, rate=16000):
    """A test 'recording': tone bursts at the given (start, end) times, cut so it begins at `start`."""
    import wave
    import numpy as np
    t = np.arange(int(dur * rate)) / rate
    sig = np.zeros_like(t)
    rng = np.random.default_rng(len(name))
    for (a, b), f, level in parts:
        m = (t >= a) & (t < b)
        sig[m] += level * np.sin(2 * np.pi * f * t[m])
    x = sig[int(start * rate):] + 0.003 * rng.standard_normal(len(sig) - int(start * rate))
    p = tmp_path / f"{name}.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return str(p)


A_TIMES = [(2, 5), (11, 13), (20, 24)]
B_TIMES = [(6, 9.5), (14, 18), (25, 28)]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_session_sync_and_crosstalk(tmp_path):
    from transcriber.session import drop_crosstalk, envelope, find_offsets
    a = [(t, 220, 0.5) for t in A_TIMES] + [(t, 330, 0.06) for t in B_TIMES]     # A's lav hears B faintly
    b = [(t, 330, 0.5) for t in B_TIMES] + [(t, 220, 0.06) for t in A_TIMES]
    cam = [(t, 220, 0.25) for t in A_TIMES] + [(t, 330, 0.25) for t in B_TIMES]
    paths = [_tone_file(tmp_path, "cam", cam), _tone_file(tmp_path, "a", a, start=3.2),
             _tone_file(tmp_path, "b", b, start=7.5)]
    offs = find_offsets(paths, 0)
    assert [round(o, 1) for o, _ in offs] == [0.0, 3.2, 7.5]
    seg_a = [Segment(x, y, "A") for x, y in A_TIMES] + [Segment(14, 18, "bleed")]
    seg_b = [Segment(x, y, "B") for x, y in B_TIMES] + [Segment(20, 24, "bleed")]
    kept = drop_crosstalk([(seg_a, offs[1][0], envelope(paths[1])), (seg_b, offs[2][0], envelope(paths[2]))])
    assert [s.text for s in kept[0]] == ["A", "A", "A"]
    assert [s.text for s in kept[1]] == ["B", "B", "B"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_isolated_recordings_assume_same_start(tmp_path):
    from transcriber.session import find_offsets
    paths = [_tone_file(tmp_path, "ia", [(t, 220, 0.5) for t in A_TIMES]),
             _tone_file(tmp_path, "ib", [(t, 330, 0.5) for t in B_TIMES])]
    assert find_offsets(paths, 0)[1] == (0.0, "start")


def test_markers_file_name():
    from transcriber.markers_ui import markers_path_for
    p = markers_path_for({"txt": Path("/x/Talk [Mic] (2)_transcript.txt")})
    assert p.name == "Talk_markers.json"


def test_session_groups_as_one_transcript(tmp_path):
    from transcriber.session import session_path, write_session
    for label in ("Victor", "Mel"):
        t = Transcript(source=str(tmp_path / f"{label}.wav"), model="tiny", language="en",
                       segments=[Segment(0, 1, "hi")], text="hi", track=label)
        save_transcript(t, tmp_path, ["txt"], name="Podcast")
    write_session(session_path(tmp_path, "Podcast"), name="Podcast", video=None,
                  files=[{"path": str(tmp_path / "Victor.wav"), "label": "Victor", "offset": 0.0},
                         {"path": str(tmp_path / "Mel.wav"), "label": "Mel", "offset": 2.0}])
    groups = group_versions(scan_transcripts(tmp_path))
    assert len(groups) == 1 and groups[0]["key"] == "Podcast"
    assert sorted(groups[0]["tracks"]) == ["Mel", "Victor"]
    assert groups[0]["session"] and groups[0]["source"].endswith("Victor.wav")


def test_base_name_and_organize(tmp_path, monkeypatch):
    from transcriber import backups
    assert files.base_name("Talk [Mic] (2)_transcript.txt") == "Talk"
    assert files.base_name("Talk (3).srt") == "Talk"
    assert files.base_name("Talk_markers.json") == "Talk"
    assert files.base_name("notes.txt") is None
    monkeypatch.setattr(backups, "BACKUP_DIR", tmp_path / "bk")
    for n in ("Talk_transcript.txt", "Talk [Mic] (2)_transcript.txt", "Talk_markers.json", "Other.srt", "notes.txt"):
        (tmp_path / n).write_text("x", encoding="utf-8")
    old_bk = backups._folder_for({"f": tmp_path / "Talk_transcript.txt"})
    old_bk.mkdir(parents=True)
    moved = files.organize_folder(tmp_path)
    assert len(moved) == 4
    assert (tmp_path / "Talk" / "Talk [Mic] (2)_transcript.txt").exists()
    assert (tmp_path / "Talk" / "Talk_markers.json").exists()
    assert (tmp_path / "Other" / "Other.srt").exists()
    assert (tmp_path / "notes.txt").exists()
    assert backups._folder_for({"f": tmp_path / "Talk" / "Talk_transcript.txt"}).exists()
    assert {g["key"] for g in files.scan_transcripts(tmp_path)} == {"Talk", "Talk [Mic] (2)", "Other"}


def test_speaker_model_offline_after_download(monkeypatch):
    import sys, types
    from transcriber import diarize_worker as worker
    from transcriber import speakers
    calls = []
    hub = types.ModuleType("huggingface_hub"); consts = types.ModuleType("huggingface_hub.constants")
    consts.HF_HUB_OFFLINE = False
    cached = {"yes": False}
    hub.try_to_load_from_cache = lambda repo, f: "/cache/config.yaml" if cached["yes"] else None
    hub.constants = consts

    class Pipeline:
        @staticmethod
        def from_pretrained(mid, token=None):
            calls.append((token, consts.HF_HUB_OFFLINE))
            return types.SimpleNamespace(to=lambda d: None)
    pa = types.ModuleType("pyannote.audio"); pa.Pipeline = Pipeline
    torch = types.ModuleType("torch"); torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    for name, mod in {"huggingface_hub": hub, "huggingface_hub.constants": consts, "pyannote": types.ModuleType("pyannote"),
                      "pyannote.audio": pa, "torch": torch}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(worker, "_pipeline", None)
    assert speakers.usable_token("") is None and speakers.usable_token(" tok ") == "tok"
    worker.load_pipeline("tok", lambda m: None)
    assert calls == [("tok", False)]                        # first time: download with the token
    cached["yes"] = True
    monkeypatch.setattr(worker, "_pipeline", None)
    assert speakers.usable_token("") == ""
    worker.load_pipeline("", lambda m: None)
    assert calls[-1] == (None, True) and consts.HF_HUB_OFFLINE is False   # offline, no token, flag restored


def test_made_up_words_and_alignment():
    text = "This section is built on a lot of different things. And it's based on three studies"
    w = [[117.8, 118.4, " This"], [118.5, 118.8, " section"], [118.9, 119.2, " is"], [119.3, 119.6, " built"],
         [119.7, 119.9, " on"], [119.9, 120.0, " a"], [120.0, 120.0, " lot"], [120.0, 120.0, " of"],
         [120.0, 120.0, " different"], [120.0, 120.0, " things."], [120.0, 120.0, " And"],
         [120.1, 120.1, " it's"], [120.1, 120.1, " based"], [120.1, 120.1, " on"], [120.1, 120.5, " three"],
         [120.6, 121.0, " studies"]]
    spans = words.suspect_spans(text, w)
    assert [text[a:b] for a, b in spans] == ["lot of different things. And it's based on"]
    assert words.remove_spans(text, spans) == "This section is built on a three studies"
    assert words.suspect_spans("edited text", w) == []
    # borrowing word times for a corrected line: matching words keep theirs, the rest are spread
    out = words.align_words("This section is built on three studies", w)
    assert [x[2] for x in out] == [" This", " section", " is", " built", " on", " three", " studies"]
    assert out[0][:2] == [117.8, 118.4] and out[-1][:2] == [120.6, 121.0]
    assert words.align_words("completely different words here", w) == []
    lines = words.retime_lines([(117.8, 121.0, "This section is built on three studies")], w)
    assert len(lines[0]) == 7
    assert core.vocab_prompt(["Lamberto", "lamberto", " Experience  Corps ", ""]) == "Glossary: Lamberto, Experience Corps."
    assert core.vocab_prompt([]) is None
    assert core.split_words("a, b\nc;; d") == ["a", "b", "c", "d"]


def test_range_marker_export():
    from fractions import Fraction
    from transcriber import marker_export as mx
    ms = [{"time": 171.6, "end": 173.1, "text": "x", "by": "claude", "note": "cut"}, {"time": 5, "text": "y", "by": "you"}]
    edl = mx.to_edl(ms, "t", Fraction(30), 0)
    assert "00:00:05:00 00:00:05:01" in edl and "|D:1" in edl
    assert "00:02:51:18 00:02:53:03" in edl and "|D:45" in edl
    x = mx.to_xmeml(ms, "t", "C:/a/b.mp4", Fraction(30), 300)
    assert "<in>5148</in><out>5193</out>" in x and "<in>150</in><out>-1</out>" in x
    assert 'duration="3/2s"' in mx.to_fcpxml(ms, "t", "C:/a/b.mp4", Fraction(30), 300)


def test_read_version_has_no_names_in_front(tmp_path):
    f = make(tmp_path, speakers=True)
    segs, labels, data = files.read_version(f)
    assert [s.text for s in segs][:2] == ["Hello there. How are you", "Second line."]
    assert labels == ["A", "B", "A"] and segs[0].words and segs[1].start == 2.0
    assert "Speaker 1: Hello" in Path(f["txt"]).read_text(encoding="utf-8")     # the .txt does have them


def test_all_formats_keep_one_order(tmp_path):
    segs = [Segment(10.6, 11.5, "AAA"), Segment(12.0, 13.0, "BBB")]
    t = Transcript(source=str(tmp_path / "o.mp4"), model="tiny", language="en", segments=segs, text="AAA BBB")
    saved = save_transcript(t, tmp_path, ["txt", "srt"])
    f = {("speakers" if p.name.endswith("_speakers.json") else "txt" if p.suffix == ".txt" else "srt"): p
         for p in saved}
    apply_text_edits(f, {}, times={1: 10.3})          # BBB moves before AAA (same whole second)
    order_txt = [ln.split("] ", 1)[1] for ln in txt_lines(f)]
    order_srt = [ln for ln in Path(f["srt"]).read_text(encoding="utf-8").splitlines() if ln in ("AAA", "BBB")]
    assert order_txt == order_srt == ["BBB", "AAA"]
    apply_text_edits(f, {0: "EDITED"})                # row 0 is BBB in every file
    assert [ln.split("] ", 1)[1] for ln in txt_lines(f)] == ["EDITED", "AAA"]
    assert "AAA" in Path(f["srt"]).read_text(encoding="utf-8") and "BBB" not in Path(f["srt"]).read_text(encoding="utf-8")


def test_copied_recording_name_is_not_a_version(tmp_path):
    for name in ("Interview", "Interview (2)"):
        t = Transcript(source=f"C:\\Videos\\{name}.mp4", model="tiny", language="en",
                       segments=[Segment(0, 1, "hi")], text="hi")
        save_transcript(t, tmp_path, ["txt"])
    t = Transcript(source="C:\\Videos\\Interview.mp4", model="large", language="en",
                   segments=[Segment(0, 1, "hi")], text="hi")
    save_transcript(t, tmp_path, ["txt"])                          # a real second version of "Interview"
    items = {g["key"]: len(g["versions"]) for g in group_versions(scan_transcripts(tmp_path))}
    assert items == {"Interview": 2, "Interview (2)": 1}
    assert files.base_name("Interview (2)_transcript.txt", keep_number=True) == "Interview (2)"
    from transcriber.markers_ui import markers_path_for
    own = tmp_path / "Interview (2)" / "Interview (2)_transcript.txt"
    assert markers_path_for({"txt": own}).name == "Interview (2)_markers.json"
    ver = tmp_path / "Interview" / "Interview (2)_transcript.txt"
    assert markers_path_for({"txt": ver}).name == "Interview_markers.json"


def test_restore_removes_sidecars_added_later(tmp_path, monkeypatch):
    from transcriber import backups
    monkeypatch.setattr(backups, "BACKUP_DIR", tmp_path / "bk")
    f = make(tmp_path, words=False)
    stamp = backups.new_stamp()
    backups.backup(f, stamp)
    sp = Path(f["txt"]).with_name(Path(f["txt"]).name.replace("_transcript.txt", "_words.json"))
    sp.write_text("{}", encoding="utf-8")                  # word timings added after the copy
    f2 = dict(f, words=sp)
    entries = [e for st, es in backups.list_backups([f2]) if st == stamp for e in es]
    backups.restore(entries)
    assert not sp.exists()


def test_every_version_has_one_master_list(tmp_path):
    f = make(tmp_path)                                   # no speakers detected
    assert f.get("speakers") and Path(f["speakers"]).exists()
    from transcriber.speakers import has_speakers
    assert not has_speakers(f)
    apply_text_edits(f, {0: "Changed."}, times={2: 1.0})
    srt = Path(f["srt"]).read_text(encoding="utf-8")
    assert txt_lines(f) == ["[00:00:00] Changed.", "[00:00:01] Third line.", "[00:00:02] Second line."]
    assert srt.index("Changed.") < srt.index("Third line.") < srt.index("Second line.")
    old = {k: v for k, v in f.items() if k != "speakers"}
    Path(f["speakers"]).unlink()                         # an older version without the list
    g = edits.ensure_lines_file(dict(old))
    assert g["speakers"].exists() and [s.text for s in files.read_version(g)[0]][0] == "Changed."


def test_clip_command_mixes_mics_on_one_timeline():
    from transcriber.review_ui import ReviewMixin
    cmd = ReviewMixin.clip_command("A.wav", 2.0, 8.0, "out.mp3", False,
                                   [{"file": "A.wav", "offset": 0.0}, {"file": "B.wav", "offset": 4.3}])
    s = " ".join(cmd)
    assert "-ss 2.000 -t 6.000 -i A.wav" in s
    assert "-ss 0.000 -t 3.700 -i B.wav" in s and "adelay=2300" in s      # B starts 2.3 s into the clip
    assert "amix=inputs=2" in s and cmd[-1] == "out.mp3"
    v = " ".join(ReviewMixin.clip_command("v.mp4", 0, 3, "o.mp4", True, [{"file": None, "stream": 1}], "c.srt", "x"))
    assert "[0:a:1]anull" in v and "subtitles=c.srt" in v and "-map [vout]" in v


def test_addon_worker_protocol(tmp_path, monkeypatch):
    """The installed app runs speaker detection in the add-on's own Python and reads its output."""
    from transcriber import addons
    fake = tmp_path / "speakers"
    py = fake / "python" / ("python.exe" if sys.platform == "win32" else "bin/python3")
    py.parent.mkdir(parents=True)
    monkeypatch.setattr(addons, "ADDON_DIR", fake)
    monkeypatch.setattr(addons, "addon_python", lambda: Path(sys.executable))
    (fake / "installed.json").write_text("{}")
    py.write_text("")
    worker = fake / "diarize_worker.py"
    worker.write_text('import json, os, sys\nprint("LOG: hello " + os.environ["HF_TOKEN"])\n'
                      'print("RESULT: " + json.dumps({"turns": [[0, 1.5, "SPEAKER_00"]], "prints": {"SPEAKER_00": [0.1]}}))\n')
    monkeypatch.setattr(addons, "_worker_source", lambda: worker)
    logs = []
    turns, prints = addons.run_diarize("x.wav", "tok", logs.append)
    assert turns == [(0, 1.5, "SPEAKER_00")] and prints == {"SPEAKER_00": [0.1]} and logs == ["hello tok"]
    worker.write_text('print("ERROR: no speech")\n')
    with pytest.raises(RuntimeError, match="no speech"):
        addons.run_diarize("x.wav", "", logs.append)


def test_setup_components_list():
    from transcriber.setup_check import components
    comps = components(vlc_ok=False)
    keys = [c["key"] for c in comps]
    assert keys[:3] == ["ffmpeg", "engine", "vlc"] and "speakers" in keys
    vlc = next(c for c in comps if c["key"] == "vlc")
    assert not vlc["ok"] and "videolan.org" in vlc["fix"]


def test_update_check(monkeypatch):
    from transcriber import addons, updater
    assert updater.parse_version("v2.10.1") > updater.parse_version("2.9.9") == (2, 9, 9)
    assets = [{"name": "Whisper-Transcriber-Setup-9.0.0.exe", "browser_download_url": "w"},
              {"name": "Whisper-Transcriber-Mac-apple-silicon.zip", "browser_download_url": "a"},
              {"name": "Whisper-Transcriber-Mac-intel.zip", "browser_download_url": "i"}]
    monkeypatch.setattr(addons, "_get_json", lambda url: {"tag_name": "v9.0.0", "body": "New things", "assets": assets,
                                                           "html_url": "page"})
    info = updater.check()
    assert info["version"] == "9.0.0" and info["notes"] == "New things"
    if sys.platform == "win32":
        assert info["asset"]["browser_download_url"] == "w"
    monkeypatch.setattr(addons, "_get_json", lambda url: {"tag_name": "v0.1.0", "assets": []})
    assert updater.check() is None                         # older than this copy: nothing to do
    assert updater.install_kind() in ("git", "source")
