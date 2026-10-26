"""Audio tracks: the tracks window, nicknames, silent tracks and which tracks play.

A mixin for the App class in app.py."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from transcriber.fonts import ui_font
from transcriber.core import audio_tracks, silent_tracks, silent_tracks_cached
from transcriber.icons import info_icon
from transcriber.ui_common import (PALETTE, save_settings,
                                   set_dark_titlebar)


class TracksMixin:

    def track_name(self, track: dict) -> str:
        """A track's display name: its nickname if you gave one, else its own name."""
        return self.track_nicknames.get(track["original"], track["original"])

    def _choice_label(self, job, choice) -> str:
        return "All tracks" if choice == "mix" else self.track_name(job["tracks"][choice])

    @staticmethod
    def _audible(job) -> list:
        """The job's audio tracks that actually have sound."""
        return [t for t in job.get("tracks") or [] if t["n"] not in job.get("silent", set())]

    def _update_job_label(self, job):
        if job.get("session"):
            self._session_label(job)
            return
        name = "  " + Path(job["path"]).name
        tracks = self._audible(job)
        if len(tracks) > 1:
            choices = job.get("choices")
            if not choices:
                name += f"   \u00b7  {len(tracks)} tracks"
            elif choices == ["mix"]:
                name += f"   \u00b7  {len(tracks)} tracks mixed"
            else:
                name += "   \u00b7  " + ", ".join(self._choice_label(job, c) for c in choices)
        self.tree.item(job["iid"], text=name)

    def _open_tracks_dialog(self, jobs, on_done=None):
        """Asked when you click Transcribe and a file has more than one audio track."""
        if not jobs:
            return
        first = jobs[0]
        audible = self._audible(first)
        silent = [t for t in first["tracks"] if t not in audible]
        win = tk.Toplevel(self)
        win.title("Audio tracks")
        win.transient(self)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=(22, 18))
        frame.pack(fill="both", expand=True)
        title = Path(first["path"]).name if len(jobs) == 1 else f"{len(jobs)} files"
        head = ttk.Frame(frame)
        head.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        ttk.Label(head, text=f"{title} has {len(audible)} audio tracks", font=ui_font(12, bold=True)).pack(side="left")
        info_icon(head, self, "Separate tracks show who said what: they're listed together, each row labeled "
                              "with its track. Mixed puts all the audio in one transcript.").pack(side="left", padx=6)

        t1 = self.track1_mix.get()
        has_t1 = any(t["n"] == 0 for t in audible)
        t1 = t1 and has_t1
        default = ["mix"] if self.track_default == "mix" else [t["n"] for t in audible if not (t1 and t["n"] == 0)]
        current = set(first.get("choices") or default)
        mix_var = tk.BooleanVar(value="mix" in current)
        mix_cb = ttk.Checkbutton(frame, variable=mix_var)
        mix_cb.grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 4))
        t1_var = tk.BooleanVar(value=t1)
        if has_t1:
            t1_row = ttk.Frame(frame)
            t1_row.grid(row=3, column=0, columnspan=3, sticky="w", pady=(0, 10))
            ttk.Checkbutton(t1_row, text="Track 1 has everything", variable=t1_var,
                            command=lambda: track1_changed()).pack(side="left")
            info_icon(t1_row, self, "Like an OBS recording's main track. Mixed then uses Track 1 as is, and Track 1 "
                                    "is skipped when transcribing tracks separately. Remembered for next time."
                      ).pack(side="left", padx=4)
        ttk.Label(frame, text="Track", style="Muted.TLabel").grid(row=4, column=0, sticky="w")
        nick_head = ttk.Frame(frame)
        nick_head.grid(row=4, column=2, sticky="w", padx=(12, 0))
        ttk.Label(nick_head, text="Nickname", style="Muted.TLabel").pack(side="left")
        info_icon(nick_head, self, "Used in place of the track's name. Applies to every file with a track of "
                                   "the same name.").pack(side="left", padx=4)

        rows = []
        track_cbs = {}
        for i, t in enumerate(audible):
            r = 5 + i
            var = tk.BooleanVar(value=t["n"] in current)
            cb = ttk.Checkbutton(frame, text=f"Track {t['n'] + 1}", variable=var)
            cb.grid(row=r, column=0, sticky="w", pady=3)
            track_cbs[t["n"]] = (cb, var)
            details = t["original"] if t["original"] != f"Track {t['n'] + 1}" else ""
            if t.get("channels") in (1, 2):
                details = (details + "  \u00b7  " if details else "") + ("mono" if t["channels"] == 1 else "stereo")
            ttk.Label(frame, text=details, style="Muted.TLabel").grid(row=r, column=1, sticky="w", padx=(8, 0))
            nick = tk.StringVar(value=self.track_nicknames.get(t["original"], ""))
            ttk.Entry(frame, textvariable=nick, width=18).grid(row=r, column=2, sticky="w", padx=(12, 0))
            rows.append((t, var, nick))

        note = ttk.Frame(frame)
        note.grid(row=6 + len(rows), column=0, columnspan=3, sticky="w", pady=(10, 0))
        if silent:
            names = ", ".join(f"Track {t['n'] + 1}" for t in silent)
            ttk.Label(note, text=f"Skipped (silent): {names}", style="Muted.TLabel").pack(side="left")
            info_icon(note, self, "These tracks have no sound at all, so they're left out of transcribing, "
                                  "mixing and playback.").pack(side="left", padx=4)

        def track1_changed():
            on = t1_var.get()
            mix_cb.configure(text="Everything (Track 1)" if on else "All tracks mixed together")
            if 0 in track_cbs:
                cb, var = track_cbs[0]
                if on:
                    var.set(False)      # it repeats the other tracks, so don't transcribe it twice
                cb.configure(state="disabled" if on else "normal")
        track1_changed()
        warn = tk.Label(frame, text="", foreground=PALETTE[self.current_theme or "light"]["err"],
                        background=ttk.Style(self).lookup("TFrame", "background") or "white", font=ui_font(9))
        warn.grid(row=7 + len(rows), column=0, columnspan=3, sticky="w")

        def save():
            choices = (["mix"] if mix_var.get() else []) + [t["n"] for t, var, _ in rows if var.get()]
            if t1_var.get() and has_t1 and 0 in choices:
                choices.remove(0)
            if not choices:
                warn.configure(text="Pick at least one: all tracks mixed, or a track.")
                return
            for t, _, nick in rows:
                name = nick.get().strip()
                if name and name != t["original"]:
                    self.track_nicknames[t["original"]] = name
                else:
                    self.track_nicknames.pop(t["original"], None)
            for job in jobs:   # other files get the same choices where they have those tracks
                job["choices"] = [c for c in choices if c == "mix" or (c < len(job["tracks"])
                                  and c not in job.get("silent", set()))] or ["mix"]
                self._update_job_label(job)
            self.track_default = "mix" if choices == ["mix"] else "each"
            if has_t1:
                self.track1_mix.set(t1_var.get())
            save_settings(self._settings())
            win.destroy()
            if on_done:
                on_done()

        buttons = ttk.Frame(frame)
        buttons.grid(row=8 + len(rows), column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Transcribe" if on_done else "Save", style="Accent.TButton",
                   command=save).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 8))
        win.bind("<Escape>", lambda e: win.destroy())
        win.bind("<Return>", lambda e: save())
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_reqheight()) // 3
        win.geometry(f"+{x}+{y}")
        set_dark_titlebar(win, self.current_theme == "dark")
        win.grab_set()

    def _player_tracks(self, source, done) -> list:
        """The source's audio tracks for the player. done = {track index: transcript track name} for
        the tracks transcribed separately, or None when every track is in the transcript (mixed)."""
        if not source or not os.path.exists(source):
            return []
        if source not in self._track_cache:
            self._track_cache[source] = audio_tracks(source)
        tracks = self._track_cache[source]
        if len(tracks) < 2:
            return []
        silent = silent_tracks_cached(source)
        if silent is None:               # not checked yet: check in the background, then refresh
            if source not in self._silence_pending:
                self._silence_pending.add(source)
                threading.Thread(target=lambda: (silent_tracks(source, len(tracks)),
                                                 self.events.put(("silence_ready", source))), daemon=True).start()
            silent = set()
        tracks = [t for t in tracks if t["n"] not in silent]
        if len(tracks) < 2:
            return []
        everything = self.track1_mix.get() and tracks[0]["n"] == 0
        return [{"n": t["n"], "name": (done or {}).get(t["n"]) or self.track_name(t),
                 "transcribed": done is None or t["n"] in done,
                 "everything": everything and t["n"] == 0} for t in tracks]

    def _on_tracks_muted(self, muted: set):
        self._muted_tracks = muted
        self._apply_muted_rows()

    def _apply_muted_rows(self):
        """Gray out the rows of muted tracks; the blue now-playing row skips them."""
        t = self.text
        t.tag_remove("mutedrow", "1.0", "end")
        muted = getattr(self, "_muted_tracks", set())
        self._muted_lines = {line for line, meta in self._row_meta.items() if meta.get("track") in muted}
        for line in self._muted_lines:
            t.tag_add("mutedrow", f"{line}.0", f"{line}.0 lineend +1c")
        if self._muted_lines:
            t.tag_raise("mutedrow")
            for tag in ("find_dim", "find", "find_cur", "sel"):
                if tag in t.tag_names():
                    t.tag_raise(tag)
            if self._now_line in self._muted_lines:
                self._set_now(None)
