"""The Transcripts tab: the list of saved transcripts, their versions and tracks, showing one,
keeping the list current, and deleting.

A mixin for the App class in app.py."""

from __future__ import annotations

import os
import hashlib
import sys
import threading
import tkinter as tk
from pathlib import Path
from typing import Optional

from transcriber.ui_common import (
    friendly_date,
    is_network_path,
    move_to_recycle_bin,
    open_path,
    show_in_folder,
)
from transcriber.speakers import rows_for
from transcriber.core import AUDIO_EXTENSIONS, audio_tracks, safe_label
from transcriber.files import folder_signature, group_versions, scan_transcripts



class SavedMixin:

    @staticmethod
    def _files_from(paths) -> dict:
        """{"txt": ..., "srt": ..., "vtt": ..., "speakers": ...} from one version's saved paths."""
        from transcriber.names import split_kind
        files = {}
        for p in map(Path, paths):
            kind = split_kind(p.name)[1]
            if kind in ("txt", "srt", "vtt", "speakers", "words"):
                files[kind] = p
        return files

    def _session_tracks(self, session_file) -> list:
        """A combined recording's files as player tracks, each starting at its own time."""
        from transcriber.session import read_session
        data = read_session(session_file) or {}
        return [{"n": i, "name": f["label"], "transcribed": f.get("transcribed", True), "file": f["path"],
                 "offset": f.get("offset", 0.0)} for i, f in enumerate(data.get("files") or [])
                if os.path.exists(f["path"])]

    @staticmethod
    def _files_for(speakers_file) -> dict:
        """The files of the version a speakers file belongs to."""
        from transcriber.names import sibling
        sp = Path(speakers_file)
        files = {"speakers": sp}
        for kind in ("txt", "srt", "vtt", "words"):
            p = sibling(sp, kind)
            if p.exists():
                files[kind] = p
        return files

    def _named_tracks(self, item) -> list:
        """Track names in the order they're in the file (Track 1 first)."""
        names = [k for k in item["tracks"] if k]
        src = item.get("source")
        if len(names) > 1 and src:
            if src not in self._track_cache:           # look at the file in the background (slow on a NAS)
                pending = self.__dict__.setdefault("_probe_pending", set())
                if src not in pending:
                    pending.add(src)

                    def probe(src=src):
                        tracks = audio_tracks(src) if os.path.exists(src) else []
                        self.events.put(("call", lambda: (self._track_cache.__setitem__(src, tracks),
                                                          pending.discard(src), self._refresh_saved(force=True))))
                    threading.Thread(target=probe, daemon=True).start()
                return names
            names.sort(key=lambda k: (self._source_track_index(src, k) is None, self._source_track_index(src, k) or 0))
        return names

    def _track_key(self, item) -> str:
        """What's showing: TRACKS (each track transcribed separately, shown together) or "" (mixed)."""
        want = self.saved_track.get(item["key"])
        named = self._named_tracks(item)
        if want == "" and "" in item["tracks"]:
            return ""
        return self.TRACKS if named else ""

    def _view_label(self, key) -> str:
        return "Mixed together" if key == "" else "Separate tracks"

    def _runs(self, item) -> list:
        """Separate-track versions grouped by the run that made them (one run transcribes each chosen
        track once, one after another). Each run looks like a version: mtime, model, files, plus parts."""
        named = self._named_tracks(item)
        all_versions = sorted(((v["mtime"], k, v) for k in named for v in item["tracks"][k]), key=lambda x: x[0])
        runs, current = [], None
        for mtime, k, v in all_versions:
            if current is None or k in current["parts"]:
                current = {"parts": {}, "mtime": mtime, "model": v.get("model", ""), "files": v["files"]}
                runs.append(current)
            current["parts"][k] = v
            current["mtime"] = mtime
        for r in runs:   # keep the tracks in a steady order
            r["parts"] = {k: r["parts"][k] for k in named if k in r["parts"]}
        return runs

    def _versions_for(self, item) -> list:
        return self._runs(item) if self._track_key(item) == self.TRACKS else item["tracks"].get("", [])

    def _current_version(self, item):
        """The version picked for the shown view (the latest unless the user chose another)."""
        versions = self._versions_for(item)
        want = self.saved_version.get(f"{item['key']}|{self._track_key(item)}")
        for v in versions:
            if v["mtime"] == want:
                return v
        return versions[-1] if versions else None

    @staticmethod
    def _version_number(versions, v) -> int:
        """1-based position of v (matched by time: separate-track versions are rebuilt on each look)."""
        return next((k for k, x in enumerate(versions, 1) if x["mtime"] == v["mtime"]), len(versions))

    def _version_label(self, item, v) -> str:
        versions = self._versions_for(item)
        n, i = len(versions), self._version_number(versions, v)
        parts = [f"Version {i}" + ("  (latest)" if i == n else "")]
        if v.get("model"):
            parts.append(v["model"])
        parts.append(friendly_date(v["mtime"]))
        return "  ·  ".join(parts)

    @staticmethod
    def _version_files(v) -> list:
        """Every file of a version (all tracks of a separate-tracks run)."""
        if "parts" in v:
            return [f for pv in v["parts"].values() for f in pv["files"].values()]
        return list(v["files"].values())

    def _source_track_index(self, source, name) -> Optional[int]:
        """Which audio track of the source file a saved track name refers to."""
        if not name or not source:
            return None
        if source not in self._track_cache:
            if not os.path.exists(source):
                return None
            self._track_cache[source] = audio_tracks(source)
        for t in self._track_cache[source]:
            if name in (self.track_name(t), t["original"], safe_label(self.track_name(t)), safe_label(t["original"])):
                return t["n"]
        return None

    @staticmethod
    def _caps_from(lines):
        return [(sum(int(x) * m for x, m in zip(ts.split(":"), (3600, 60, 1))), text) for ts, text in lines]

    def _show_saved(self, iid):
        self.display = ("saved", iid)
        item = self.saved_items.get(iid)
        if not item:
            self.player.load(None)
            self._render("Transcript", [("Select a transcript to read it here.\n", "info")])
            return
        self._ensure_lines(item)          # older versions get their master list of lines
        self._ensure_words(item)          # versions without word timings borrow them from another
        key = self._track_key(item)
        named = self._named_tracks(item)

        # View picker: only when this video has both separate-track and mixed transcripts.
        if named and "" in item["tracks"]:
            if not self.track_cb.winfo_manager():
                at = self.version_title if self.version_title.winfo_manager() else self.version_info
                self.track_title.pack(side="left", padx=(2, 8), before=at)
                self.track_cb.pack(side="left", padx=(0, 14), before=at)
            labels = [self._view_label(self.TRACKS), self._view_label("")]
            self.track_cb.configure(values=labels, width=max(len(x) for x in labels) + 2)
            self.track_var.set(self._view_label(key))
        else:
            self.track_title.pack_forget()
            self.track_cb.pack_forget()

        source = item.get("source")
        note = (f"The original file isn't available:\n{source}" if source else
                "This transcript doesn't say which file it came from")
        playable = source if source and os.path.exists(source) else None
        self._export_source = source

        v = self._current_version(item)
        if key == self.TRACKS:
            parts = [(k, pv["files"]) for k, pv in v["parts"].items()]
            entries, caps = self._merged_entries(parts)
            track_names = " + ".join(v["parts"])
            fmts = " · ".join(f.upper() for f in ("txt", "srt", "vtt")
                                   if any(f in pv["files"] for pv in v["parts"].values()))
            empty = not caps
        else:
            files = v["files"]
            rows = rows_for(files, self.people)
            entries = self._view_entries([(r, files, "", 0, k) for k, r in enumerate(rows)])
            caps = [self._cap(r) for r in rows]
            track_names = ""
            fmts = " · ".join(f.upper() for f in ("txt", "srt", "vtt") if f in files)
            empty = not rows
        v["caps"] = caps

        versions = self._versions_for(item)
        if len(versions) > 1:
            if not self.version_cb.winfo_ismapped():
                self.version_title.pack(side="left", padx=(2, 8), before=self.version_info)
                self.version_cb.pack(side="left", before=self.version_info)
            self.version_cb.configure(values=[self._version_label(item, x) for x in reversed(versions)])
            self.version_var.set(self._version_label(item, v))
            self.version_info.configure(text="  ·  ".join(x for x in (track_names, fmts) if x))
            self._del_tip = "Delete this version"
            self.version_del_btn.configure(state="normal")
        else:
            self.version_title.pack_forget()
            self.version_cb.pack_forget()
            details = [x for x in (track_names, v.get("model"), friendly_date(v["mtime"]), fmts) if x]
            self.version_info.configure(text="  ·  ".join(details))
            only = len(versions) == 1 and not (named and "" in item["tracks"])
            self._del_tip = "Delete this transcript" if only else "Delete this version"
            self.version_del_btn.configure(state="normal")

        # Person button: only for transcripts made without speaker detection.
        from transcriber.speakers import has_speakers
        if key == self.TRACKS:
            lacking = [k for k, pv in v["parts"].items() if not has_speakers(pv["files"])]
        else:
            lacking = [""] if not has_speakers(v["files"]) else []
        self._stats_parts = ([(k, pv["files"]) for k, pv in v["parts"].items()] if key == self.TRACKS
                             else [("", v["files"])])
        if any(has_speakers(f) for _k, f in self._stats_parts) or len(self._stats_parts) > 1:
            if not self.stats_btn.winfo_ismapped():
                self.stats_btn.pack(side="right", padx=(0, 6), after=self.version_del_btn)
        else:
            self.stats_btn.pack_forget()
        if lacking and not self.speakers_btn.winfo_ismapped():
            self.speakers_btn.pack(side="right", padx=(0, 6), after=self.version_del_btn)
        elif not lacking:
            self.speakers_btn.pack_forget()
        self.speakers_btn.configure(state="disabled" if self._speakers_busy else "normal")

        self.player.load(playable, lambda v=v: v["caps"], note=note)
        self.player.captions = lambda v=v: v["caps"]   # same file, other version: just swap captions
        self._pcaps_parts = ([(k, pv["files"]) for k, pv in v["parts"].items()] if key == self.TRACKS
                             else [("", v["files"])])
        self._pcaps_v = v
        v["pcaps"] = self._preview_caps(self._pcaps_parts)
        self.player.display_captions = (lambda v=v: v["pcaps"]) if v["pcaps"] else None
        self.player.refresh_captions()
        if playable and item.get("session"):
            self.player.set_tracks(self._session_tracks(item["session"]))
        elif playable:
            if key == self.TRACKS:
                done = {self._source_track_index(source, k): k for k in v["parts"]}
                self.player.set_tracks(self._player_tracks(source, done))
            else:
                self.player.set_tracks(self._player_tracks(source, None))   # mixed: every track is in it
        self._set_call(playable, caps, list(v["parts"]) if key == self.TRACKS and v.get("parts") else None)
        if empty:
            entries.append(("This transcript is empty or couldn't be read.\n", "err"))
        self._render(item["key"], entries, scroll_end=False, versions=True)

    def _on_track_picked(self, _event=None):
        sel = self.saved.selection()
        item = self.saved_items.get(sel[0]) if sel else None
        if not item:
            return
        self.saved_track[item["key"]] = "" if self.track_var.get() == self._view_label("") else self.TRACKS
        self._show_saved(sel[0])

    def _on_version_picked(self, _event=None):
        sel = self.saved.selection()
        item = self.saved_items.get(sel[0]) if sel else None
        if not item:
            return
        versions = self._versions_for(item)
        labels = [self._version_label(item, x) for x in versions]
        if self.version_var.get() in labels:
            self.saved_version[f"{item['key']}|{self._track_key(item)}"] = versions[labels.index(self.version_var.get())]["mtime"]
            self._show_saved(sel[0])

    def _on_saved_select(self):
        sel = self.saved.selection()
        self._show_saved(sel[0] if sel else None)

    def _refresh_saved(self, force: bool = False, fs_sig=None):
        folder = self.out_var.get()
        if folder != getattr(self, "_organized", None):      # loose files from older versions: into folders
            self._organized = folder
            from transcriber.files import organize_folder
            organize_folder(folder)
        sig = (folder, self._search_text(), folder_signature(folder) if fs_sig is None else fs_sig)
        if sig == self.saved_sig and not force:
            return
        self.saved_sig = sig
        sel = self.saved.selection()
        selected_key = self.saved_items[sel[0]]["key"] if sel and sel[0] in self.saved_items else None

        for iid in self.saved.get_children():
            self.thumb_images.pop(iid, None)
        self.saved.delete(*self.saved.get_children())
        self.saved_items.clear()

        needle = self._search_text().strip().lower()
        transcripts = group_versions(scan_transcripts(folder))
        if needle:      # match the name or anything said in it
            shown = [t for t in transcripts if needle in t["key"].lower()
                     or any(needle in r[2].lower() for r in self._content_rows(t))]
        else:
            shown = transcripts
        reselect = None
        for tr in shown:
            iid = "s" + hashlib.sha1(tr["key"].encode("utf-8", "replace")).hexdigest()[:16]
            src = tr["source"]
            if src and os.path.exists(src):
                icon = "audio" if Path(src).suffix.lower() in AUDIO_EXTENSIONS else "pending"
            else:
                icon = "doc"
            self.saved.insert("", "end", iid=iid, text="  " + tr["key"], image=self.icons[icon],
                              values=(len(tr["tracks"].get("", [])) + len(self._runs(tr)), friendly_date(tr["mtime"])))
            self.saved_items[iid] = {**tr, "icon": icon}
            if icon == "pending":
                self.thumb_q.put(("saved", iid, src))
            if tr["key"] == selected_key:
                reselect = iid

        where = Path(folder).name or folder
        if needle:
            self.saved_count_var.set(f"{len(shown)} of {len(transcripts)} transcripts match")
        else:
            self.saved_count_var.set(f"{len(transcripts)} transcript{'s' if len(transcripts) != 1 else ''} in {where}"
                                     if transcripts else f"No transcripts in {where} yet")
        self._search_shown = shown
        if needle and (self._search_dirty or (self.display and self.display[0] == "search")):
            self._search_dirty = False
            self._show_search_results(needle, shown)
            return
        self._search_dirty = False
        if reselect:
            self.saved.selection_set(reselect)
            if self.display and self.display[0] == "saved":
                self._show_saved(reselect)   # versions may have changed even though the row didn't
        elif self.display and self.display[0] in ("saved", "search"):
            self._show_saved(None)

    def _watch_saved_folder(self):
        """Keep the Transcripts list current even while on the Queue tab, so it never has to load
        when you switch to it. Looking through the folder (slow on a NAS) happens in the background;
        the list only rebuilds when files actually changed."""
        try:
            self._check_markers()          # markers added from outside (e.g. by Claude)
        except tk.TclError:
            pass
        self.after(3000, self._watch_saved_folder)
        if getattr(self, "_watch_busy", False):
            return
        self._watch_busy = True
        folder = self.out_var.get()

        def work():
            try:
                sig = folder_signature(folder)
            except Exception:
                sig = None
            self.events.put(("call", lambda: self._watch_done(folder, sig)))
        threading.Thread(target=work, daemon=True).start()

    def _watch_done(self, folder, sig):
        self._watch_busy = False
        if sig is not None and folder == self.out_var.get():
            self._refresh_saved(fs_sig=sig)

    def _selected_saved(self):
        sel = self.saved.selection()
        return self.saved_items.get(sel[0]) if sel else None

    def _open_saved(self):
        item = self._selected_saved()
        if item:
            f = self._current_version(item)["files"]
            open_path(f.get("txt") or f.get("srt") or f.get("vtt"))

    def _delete_detail(self, path) -> tuple[str, bool]:
        network = is_network_path(path)
        if network:
            return "This folder is on a network drive, so these files will be deleted permanently.", True
        if sys.platform == "win32":
            return "They'll go to the Recycle Bin, so you can restore them if you change your mind.", False
        if sys.platform == "darwin":
            return "They'll go to the Trash, so you can put them back if you change your mind.", False
        return "This can't be undone.", False

    def _network_delete_blocked(self, path) -> bool:
        """True (and explain) if this is on a network drive and network deletes are turned off."""
        if not is_network_path(path) or self.allow_network_delete.get():
            return False
        if self._confirm(
            title="Deleting is off for network drives",
            heading="Deleting on network drives is turned off",
            body="",
            items=[],
            detail="Your transcripts folder is on a network drive (like your NAS). Files there can't go to "
                   "the Recycle Bin, so a delete would be permanent. To allow it, turn on \u201cAllow deleting "
                   "transcripts on network drives\u201d in Settings.",
            confirm="Open Settings",
            cancel="OK",
        ):
            self._open_settings()
        return True

    def _delete_files(self, files, what: str):
        if self.display and self.display[0] == "saved":
            self.player.load(None)
        folders = {Path(f).parent for f in files}
        ok = move_to_recycle_bin(files)
        out = Path(self.out_var.get())
        for d in folders:      # a recording's folder with nothing left but its markers: tidy it away
            try:
                if d != out and d.parent == out and d.is_dir():
                    rest = list(d.iterdir())
                    if all(r.name.lower().endswith("_markers.json") for r in rest):
                        if rest:
                            move_to_recycle_bin(rest)
                        d.rmdir()
            except OSError:
                pass
        self._refresh_saved(force=True)
        self.status_var.set(f"Deleted {what}." if ok else
                            f"Couldn't delete every file for {what}. One may be open in another program.")

    def _delete_saved(self):
        """Delete a transcript and every version of it."""
        item = self._selected_saved()
        if not item:
            return
        everything = [v for vs in item["tracks"].values() for v in vs]
        files = [f for v in everything for f in v["files"].values()]
        if self._network_delete_blocked(files[0]):
            return
        detail, network = self._delete_detail(files[0])
        n = len(everything)
        if not self._confirm(
            title="Delete transcript?",
            heading=f"Delete \u201c{item['key']}\u201d" + (f" and all {n} versions?" if n > 1 else "?"),
            body="These files will be removed:",
            items=[f.name for f in files],
            detail=detail,
            confirm="Delete permanently" if network else ("Delete all" if n > 1 else "Delete"),
        ):
            return
        self.saved_track.pop(item["key"], None)
        self._delete_files(files, f"\u201c{item['key']}\u201d")

    def _delete_version(self):
        """Delete only the version shown in the version picker."""
        item = self._selected_saved()
        if not item:
            return
        v = self._current_version(item)
        if v is None:
            return
        files = self._version_files(v)
        if self._network_delete_blocked(files[0]):
            return
        detail, network = self._delete_detail(files[0])
        key = self._track_key(item)
        versions = self._versions_for(item)
        n, i = len(versions), self._version_number(versions, v)
        both = bool(self._named_tracks(item)) and "" in item["tracks"]
        last = n == 1 and not both
        view = "separate-tracks" if key == self.TRACKS else "mixed"
        track_part = f" ({view})" if both else ""
        if not self._confirm(
            title="Delete version?",
            heading=(f"Delete \u201c{item['key']}\u201d?" if last else
                     f"Delete version {i}{track_part} of \u201c{item['key']}\u201d?" if n > 1 else
                     f"Delete the {view} transcript of \u201c{item['key']}\u201d?"),
            body="These files will be removed:",
            items=[f.name for f in files],
            detail=detail + ("" if not last else " The transcript will disappear from the list."),
            confirm="Delete permanently" if network else "Delete version",
        ):
            return
        self.saved_version.pop(f"{item['key']}|{key}", None)    # fall back to the latest remaining version
        if n == 1:
            self.saved_track.pop(item["key"], None)            # that track is gone; show another one
        self._delete_files(files, f"version {i}{track_part} of \u201c{item['key']}\u201d")

    def _show_saved_in_folder(self):
        item = self._selected_saved()
        if item:
            f = self._current_version(item)["files"]
            show_in_folder(f.get("txt") or f.get("srt") or f.get("vtt"))
        else:
            self._open_output_dir()
