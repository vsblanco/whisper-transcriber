import sys
import threading

from transcriber import crashlog as C


def _point_at(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "LOG_DIR", tmp_path)
    monkeypatch.setattr(C, "ERRORS", tmp_path / "errors.log")
    monkeypatch.setattr(C, "LAST_RUN", tmp_path / "last_run.log")
    monkeypatch.setattr(C, "previous_crash", None)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    monkeypatch.setattr(sys, "stderr", sys.stderr)


def test_errors_are_logged_with_context(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch)
    C.start(lambda: "saved 'Interview', timeline=on")
    try:
        1 / 0
    except ZeroDivisionError as exc:
        C.log_error("Error in the window", exc)
    text = (tmp_path / "errors.log").read_text(encoding="utf-8")
    assert "ZeroDivisionError" in text and "timeline=on" in text and "Error in the window" in text
    C.stop()


def test_a_run_that_did_not_close_normally_is_kept(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch)
    (tmp_path / "last_run.log").write_text("=== started ===\nFatal Python error: Segmentation fault\n")
    C.start()
    assert C.previous_crash is not None and "Segmentation fault" in C.previous_crash.read_text()
    C.stop()                                   # this run closes normally...
    monkeypatch.setattr(C, "previous_crash", None)
    C.start()
    assert C.previous_crash is None            # ...so the next start finds nothing to report
    C.stop()
