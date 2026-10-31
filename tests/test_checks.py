from transcriber.captions import Cue, Line, build
from transcriber.checks import check_all, check_cues, check_lines


def test_checks_find_what_viewers_trip_over():
    cues = [Cue(0.0, 0.3, ["Hi there everybody, welcome back."], src=["a"]),        # too short, too fast
            Cue(1.0, 2.0, ["A short one."], src=["b"]),
            Cue(1.8, 3.0, ["Overlapping the one before."], src=["c"]),
            Cue(4.0, 13.0, ["Hangs around."], src=["d"]),
            Cue(14.0, 16.0, ["x" * 50], src=["e"]),
            Cue(1.9, 2.5, ["Someone else, another spot."], layer=1, src=["f"])]
    kinds = [(p["kind"], p["src"]) for p in check_cues(cues)]
    assert ("Reads too fast", "a") in kinds and ("Too short", "a") in kinds
    assert ("Overlaps the one before", "c") in kinds and ("Overlaps the one before", "f") not in kinds
    assert ("Up too long", "d") in kinds and ("Line too long", "e") in kinds
    assert not [k for k in kinds if k[1] == "b"]
    lines = [("l1", 0.0, 1.0, "and and and and we went"), ("l2", 2.0, 3.0, "I know I know I know I know."),
             ("l3", 4.0, 5.0, "fine fine"), ("l4", 6.0, 7.0, "w " * 200)]
    found = {p["src"]: p["kind"] for p in check_lines(lines)}
    assert found == {"l1": "Repeated words", "l2": "Repeated words", "l4": "Very long line"}
    # what the app builds normally passes clean
    w = [(t / 3, t / 3 + 0.3, x) for t, x in enumerate("This is a normal caption that reads fine.".split())]
    assert check_all(build([Line(0, 3.0, "This is a normal caption that reads fine.", words=w, src="ok")]), []) == []
