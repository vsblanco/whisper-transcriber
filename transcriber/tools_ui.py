"""Talk backups and adding speakers to an existing transcript.

A mixin for the App class in app.py."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import ttk

from transcriber.edits import add_speakers
from transcriber.icons import info_icon
from transcriber.speakers import detection_status, rows_for
from transcriber.ui_common import set_dark_titlebar
from transcriber.fonts import ui_font


class ToolsMixin:

    def _backup(self, files, stamp=None):
        """Copy a version's files before changing them (restore from the history button)."""
        from transcriber.backups import backup
        try:
            backup(files, stamp)
        except OSError:
            pass

    def _show_history(self):
        """Pick an earlier copy of the shown transcript to go back to."""
        from transcriber.backups import list_backups, stamp_label
        parts = [f for _k, f in getattr(self, "_stats_parts", [])]
        if not parts:
            return
        found = list_backups(parts)
        p = self._palette()
        dark = self.current_theme == "dark"
        menu = tk.Menu(self, tearoff=0, background="#2b2b2b" if dark else "#ffffff", foreground=p["fg"],
                       activebackground=p["hover"], activeforeground=p["fg"], disabledforeground=p["muted"],
                       font=ui_font(10), relief="flat", borderwidth=1)
        if not found:
            menu.add_command(label="No earlier copies yet", state="disabled")
        else:
            menu.add_command(label="Go back to the copy from...", state="disabled")
            for stamp, entries in found[:20]:
                menu.add_command(label=stamp_label(stamp), command=lambda e=entries, st=stamp: self._restore(e, st))
        b = self.history_btn
        try:
            menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())
        finally:
            menu.grab_release()

    def _restore(self, entries, stamp):
        from transcriber.backups import restore, stamp_label
        if self._edits and not self._confirm(
                title="Restore?", heading="Discard your unsaved changes and restore?", body="", items=[],
                detail="Your unsaved edits will be lost.", confirm="Restore"):
            return
        if not self._confirm(title="Restore?", heading=f"Go back to the copy from {stamp_label(stamp)}?",
                             body="", items=[], detail="The current version is kept as a copy too, so you can "
                             "come back to it from this same menu.", confirm="Restore"):
            return
        self._end_edit()
        self._edits, self._hist, self._hist_i, self._split_undo = {}, [{}], 0, []
        self._update_edit_bar()
        restore(entries)
        self.status_var.set(f"Restored the copy from {stamp_label(stamp)}.")
        self._refresh_saved(force=True)
        self._redisplay()

    def _talk_stats(self, parts) -> list:
        """[(label, color, seconds, person or None)] most talk first, from each part's lines."""
        from transcriber.speakers import display_names, read_speakers_file
        totals: dict = {}
        many = len(parts) > 1
        for track, files in parts:
            data = read_speakers_file(files["speakers"]) if files.get("speakers") else None
            if data and any(s.get("speaker") for s in data.get("segments", [])):
                names = display_names(data, self.people)
                for seg in data["segments"]:
                    lab = seg.get("speaker")
                    person = self.people.get(((data.get("speakers") or {}).get(lab) or {}).get("person"))
                    if person:
                        key, label, color = person["id"], person["name"], person["color"]
                    else:
                        name = names.get(lab, "Unknown") if lab else "Unknown"
                        key, label, color = (name, track), (f"{name} ({track})" if many else name), "#9aa5b1"
                    entry = totals.setdefault(key, [label, color, 0.0, person])
                    entry[2] += max(float(seg["end"]) - float(seg["start"]), 0)
            else:
                rows = rows_for(files, self.people)
                for k, r in enumerate(rows):
                    nxt = rows[k + 1][0] if k + 1 < len(rows) else r[0] + 3
                    entry = totals.setdefault(("track", track), [track or "Everyone", "#64748b", 0.0, None])
                    entry[2] += max(min(nxt - r[0], 30), 0)
        return sorted((tuple(v) for v in totals.values()), key=lambda x: -x[2])

    def _show_talk_time(self):
        parts = getattr(self, "_stats_parts", None)
        item = self._selected_saved()
        if not parts or not item:
            return
        stats = self._talk_stats(parts)
        total = sum(x[2] for x in stats) or 1
        win = tk.Toplevel(self)
        win.title("Talk time")
        win.transient(self)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=(22, 18))
        frame.pack(fill="both", expand=True)
        head = ttk.Frame(frame)
        head.pack(fill="x", pady=(0, 12))
        ttk.Label(head, text=f"Talk time \u00b7 {item['key']}", style="Section.TLabel").pack(side="left")
        info_icon(head, self, "How long each person spoke, added up from their lines. Lines without a "
                              "detected speaker count as Unknown.").pack(side="left", padx=6)
        p = self._palette()
        size = max(int(26 * self.ui_scale), 22)
        bar_w = int(220 * self.ui_scale)
        name_w = min(max([len(x[0]) for x in stats] + [8]) + 2, 34)
        for label, color, secs, person in stats:
            row = ttk.Frame(frame)
            row.pack(fill="x", pady=4)
            stand_in = person or {"id": label, "name": label, "color": color, "photo": ""}
            ttk.Label(row, image=self.avatars.get(stand_in, size, label, color)).pack(side="left")
            ttk.Label(row, text=label, width=name_w).pack(side="left", padx=(8, 8))
            c = tk.Canvas(row, width=bar_w, height=10, highlightthickness=0, background=p["band"])
            c.pack(side="left")
            c.create_rectangle(0, 0, max(int(bar_w * secs / total), 2), 10, width=0, fill=color)
            m, sec = divmod(int(round(secs)), 60)
            h, m = divmod(m, 60)
            dur = f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
            ttk.Label(row, text=f"{dur}   {secs / total:.0%}", style="Muted.TLabel", width=12,
                      anchor="e").pack(side="left", padx=(10, 0))
        ttk.Button(frame, text="Done", style="Accent.TButton", command=win.destroy).pack(anchor="e", pady=(14, 0))
        win.bind("<Escape>", lambda e: win.destroy())
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_reqheight()) // 3
        win.geometry(f"+{x}+{y}")
        set_dark_titlebar(win, self.current_theme == "dark")
        win.grab_set()

    def _add_speakers_later(self):
        """Run speaker detection on the shown transcript now (it was made with detection off)."""
        item = self._selected_saved()
        if not item or self._speakers_busy:
            return
        from transcriber.speakers import usable_token
        token = usable_token(self.hf_token.get())
        ok, msg = detection_status()
        if ok and token is None:
            ok, msg = False, "Add your Hugging Face token in Settings > Speakers first."
        source = item.get("source")
        if ok and not (source and os.path.exists(source)):
            ok, msg = False, (f"The original file is needed to hear the voices, and it isn't here anymore:\n{source}"
                              if source else "This transcript doesn't say which file it came from.")
        if not ok:
            installed = detection_status()[0]
            if self._confirm(title="Detect speakers", heading="Can't detect speakers yet", body="", items=[],
                             detail=msg, confirm="OK" if installed else "What's installed...",
                             cancel=None if installed else "Close") and not installed:
                self._open_setup("speakers")
            return
        key, v = self._track_key(item), self._current_version(item)
        if key == self.TRACKS:
            from transcriber.speakers import has_speakers
            parts = [(k, pv["files"]) for k, pv in v["parts"].items() if not has_speakers(pv["files"])]
        else:
            parts = [("", v["files"])]
        if getattr(self, "_edits", None):
            self.status_var.set("Save or discard your edits first, then detect speakers.")
            return
        self._speakers_busy = True
        self.speakers_btn.configure(state="disabled")
        self._busy(True, "Detecting speakers")
        name = item["key"]

        def work():
            err = None
            for k, files in parts:
                label = f" ({k})" if k else ""
                try:
                    idx = self._source_track_index(source, k) if k else None
                    self._backup(files)
                    add_speakers(files, source, idx, k, token, self.people,
                                 log=lambda m, label=label: self.events.put(("status_text", f"{name}{label}: {m}")))
                except Exception as exc:
                    err = f"{k + ': ' if k else ''}{exc}"
            self.events.put(("speakers_added", name, err))

        self.status_var.set(f"Detecting speakers in {name}...")
        threading.Thread(target=work, daemon=True).start()
