"""The Resolve bridge against a stand-in for Resolve's scripting objects (the real one needs a
running DaVinci Resolve)."""
from transcriber import resolve_bridge as rb
from transcriber.captions import Cue


class Tool:
    def __init__(self):
        self.inputs = {}

    def SetInput(self, key, value):
        self.inputs[key] = value


class Comp:
    def __init__(self, tool):
        self.tool = tool

    def FindToolByID(self, kind):
        return self.tool if kind == "TextPlus" else None


class Item:
    def __init__(self, info, has_text=True):
        self.info, self.tool = info, Tool() if has_text else None

    def GetStart(self):
        return self.info["recordFrame"]

    def GetFusionCompCount(self):
        return 1 if self.tool else 0

    def GetFusionCompByIndex(self, i):
        return Comp(self.tool)


class Clip:
    def __init__(self, name, kind="Video"):
        self.props = {"Clip Name": name, "Type": kind}

    def GetClipProperty(self, key=None):
        return self.props if key is None else self.props.get(key)


class Folder:
    def __init__(self, clips, subs=()):
        self.clips, self.subs = clips, list(subs)

    def GetClipList(self):
        return self.clips

    def GetSubFolderList(self):
        return self.subs


class Pool:
    def __init__(self, root):
        self.root, self.appended, self.imported = root, [], []

    def GetRootFolder(self):
        return self.root

    def ImportMedia(self, paths):
        self.imported += paths
        return [Clip(p, "Subtitle") for p in paths]

    def AppendToTimeline(self, infos):
        self.appended.append(infos)
        return [Item(i) if isinstance(i, dict) else Item({"recordFrame": 0}) for i in infos]


class Timeline:
    def __init__(self):
        self.tracks = {"video": 2, "subtitle": 0}

    def GetSetting(self, key):
        return "30"

    def GetStartFrame(self):
        return 108000

    def GetEndFrame(self):
        return 108000 + 30 * 600

    def GetName(self):
        return "Episode 4"

    def GetTrackCount(self, kind):
        return self.tracks[kind]

    def AddTrack(self, kind):
        self.tracks[kind] += 1
        return True


class Project:
    def __init__(self, pool):
        self.pool, self.timeline = pool, Timeline()

    def GetCurrentTimeline(self):
        return self.timeline

    def GetMediaPool(self):
        return self.pool

    def GetSetting(self, key):
        return "30"


class Resolve:
    def __init__(self, project):
        self.project = project

    def GetProjectManager(self):
        return self

    def GetCurrentProject(self):
        return self.project


class DVR:
    def __init__(self, resolve):
        self.resolve = resolve

    def scriptapp(self, name):
        return self.resolve


def _setup(with_template=True):
    clips = [Clip("A-roll.mp4")] + ([Clip("Text+", "Generator")] if with_template else [])
    pool = Pool(Folder([Clip("music.wav")], [Folder(clips)]))
    return pool, rb.connect(lambda: DVR(Resolve(Project(pool))))


def test_connect_says_what_to_do_when_resolve_is_not_there():
    import pytest
    with pytest.raises(rb.ResolveError, match="scripting module wasn't found"):
        rb.connect(lambda: None)
    with pytest.raises(rb.ResolveError, match="External scripting"):
        rb.connect(lambda: DVR(None))


def test_text_plus_captions_land_on_a_new_track_in_each_persons_color():
    pool, resolve = _setup()
    info = rb.open_timeline(resolve)
    assert info["fps"] == 30 and info["start"] == 108000 and info["name"] == "Episode 4"
    cues = [Cue(1.0, 2.5, ["Hello there"], "Mel", color="#ff0000"),
            Cue(2.5, 4.0, ["and welcome", "back"], "Cam", color="#0000ff"),
            Cue(3.0, 4.5, ["Hi!"], "Mel", color="#ff0000", layer=1)]
    r = rb.send_text_plus(info, cues, colors=True, size=0.06)
    assert r == {"placed": 3, "styled": 3, "track": 3}             # tracks 3 (and 4 for the stacked one)
    infos = pool.appended[0]
    assert [(i["recordFrame"], i["endFrame"], i["trackIndex"]) for i in infos] == \
        [(108030, 44, 3), (108075, 44, 3), (108090, 44, 4)]
    assert all(i["mediaPoolItem"].props["Clip Name"] == "Text+" for i in infos)


def test_styling_puts_words_and_colors_into_the_title():
    item = Item({"recordFrame": 0})
    assert rb.style_item(item, "Hello\nthere", "#ff8000", 0.07)
    assert item.tool.inputs["StyledText"] == "Hello\nthere"
    assert abs(item.tool.inputs["Red1"] - 1.0) < 1e-6 and abs(item.tool.inputs["Green1"] - 0.502) < 0.01
    assert item.tool.inputs["Blue1"] == 0.0 and item.tool.inputs["Size"] == 0.07
    assert not rb.style_item(Item({"recordFrame": 0}, has_text=False), "x")


def test_no_template_and_subtitle_track():
    import pytest
    pool, resolve = _setup(with_template=False)
    info = rb.open_timeline(resolve)
    with pytest.raises(rb.ResolveError, match="drag Text\\+"):
        rb.send_text_plus(info, [Cue(0, 1, ["x"])])
    assert rb.send_subtitle_track(info, "/tmp/captions.srt") == 1
    assert pool.imported == ["/tmp/captions.srt"] and info["timeline"].tracks["subtitle"] == 1
