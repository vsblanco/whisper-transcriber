"""People, speaker names, photos and voice samples.

A mixin for the App class in app.py."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk


from transcriber.core import write_text_safe
from transcriber.icons import Tooltip, icon_button, info_icon
from transcriber.speakers import detection_status
from transcriber.ui_common import PALETTE, set_dark_titlebar
from transcriber.fonts import ui_font


class PeopleMixin:

    def _on_who_click(self, event):
        line = int(self.text.index(f"@{event.x},{event.y}").split(".")[0])
        who = self._who_by_line.get(line)
        if not who or not who[0].get("speakers"):
            return "break"
        self._show_who_menu(event.x_root, event.y_root, *who)
        return "break"

    def _show_who_menu(self, x, y, files, label):
        from transcriber.speakers import display_names, read_speakers_file
        data = read_speakers_file(files["speakers"]) or {}
        current = ((data.get("speakers") or {}).get(label) or {}).get("person")
        shown_name = display_names(data, self.people).get(label, label) if data else label
        mode = self.current_theme or "light"
        p = PALETTE[mode]
        bg, hover, border = ("#ffffff", "#ebedf0", "#d0d0d0") if mode == "light" else ("#2b2b2b", "#383838", "#454545")
        self._close_who()
        pop = tk.Toplevel(self)
        self._who_popup = pop
        pop.overrideredirect(True)
        pop.configure(background=border, padx=1, pady=1)
        box = tk.Frame(pop, background=bg, padx=4, pady=6)
        box.pack(fill="both", expand=True)
        tk.Label(box, text=f"Who is “{shown_name}”?", background=bg, foreground=p["muted"],
                 font=ui_font(9), anchor="w").pack(fill="x", padx=8, pady=(0, 4))
        size = max(int(22 * self.ui_scale), 18)

        def item(text, command, image=None, checked=False, muted=False):
            row = tk.Frame(box, background=bg, cursor="hand2")
            row.pack(fill="x")
            parts = [row]
            if image is not None:
                il = tk.Label(row, image=image, background=bg)
                il.pack(side="left", padx=(8, 6), pady=3)
                parts.append(il)
            tl = tk.Label(row, text=text + ("   ✓" if checked else ""), background=bg, anchor="w",
                          foreground=p["muted"] if muted else p["fg"],
                          font=ui_font(10, bold=checked))
            tl.pack(side="left", fill="x", expand=True, padx=(0 if image is not None else 12, 16), pady=4)
            parts.append(tl)
            for w in parts:
                w.bind("<Enter>", lambda e, ps=parts: [q.configure(background=hover) for q in ps])
                w.bind("<Leave>", lambda e, ps=parts: [q.configure(background=bg) for q in ps])
                w.bind("<Button-1>", lambda e: (self._close_who(), command()))

        for person in sorted(self.people.people, key=lambda q: q["name"].lower()):
            item(person["name"], lambda pid=person["id"]: self._assign_speaker(files, label, pid),
                 image=self.avatars.get(person, size), checked=person["id"] == current)
        if self.people.people:
            tk.Frame(box, background=border, height=1).pack(fill="x", pady=4, padx=6)
        item("New person...", lambda: self._new_person_dialog(lambda pid: self._assign_speaker(files, label, pid)))
        if current:
            item("Not them (back to a number)", lambda: self._assign_speaker(files, label, None), muted=True)

        pop.update_idletasks()
        pop.geometry(f"+{x}+{y + 8}")
        pop.bind("<Escape>", lambda e: self._close_who())
        pop.focus_force()
        # Close when clicking anywhere else.
        self.after(50, lambda: self.bind_all("<Button-1>", self._maybe_close_who, add="+"))

    def _maybe_close_who(self, event):
        pop = getattr(self, "_who_popup", None)
        if pop is not None and pop.winfo_exists() and not str(event.widget).startswith(str(pop)):
            self._close_who()

    def _close_who(self):
        pop = getattr(self, "_who_popup", None)
        if pop is not None:
            try:
                pop.destroy()
            except tk.TclError:
                pass
            self._who_popup = None
            self.unbind_all("<Button-1>")

    def _assign_speaker(self, files, label, pid):
        """Name a detected speaker: relabel their lines, rewrite the files, and learn the voice."""
        import json
        from transcriber.edits import rewrite_from_speakers
        from transcriber.speakers import read_speakers_file
        data = read_speakers_file(files["speakers"])
        if not data or label not in data.get("speakers", {}):
            return
        info = data["speakers"][label]
        info["person"] = pid
        self._backup(files)
        write_text_safe(files["speakers"], json.dumps(data))
        person = self.people.get(pid)
        if info.get("voiceprint"):
            self.people.forget_voice(info["voiceprint"], keep_pid=pid)   # renamed: not the earlier person's
        learned = False
        if person and info.get("voiceprint") and self.people.usable_print(info["voiceprint"]):
            clip = self._voice_clip(data, label)
            if clip is None or clip["talk"] >= 1.5:     # too little talking makes a poor sample
                learned = True
                self._learn_from_transcript(pid, info["voiceprint"], clip)
        rewrite_from_speakers(files["speakers"], files, self.people)
        self._redisplay()
        self._refresh_saved(force=True)
        if person:
            self.status_var.set(f"Labeled as {person['name']}. " + (
                "New recordings will recognize this voice." if learned else
                "(Too little talking here to learn the voice from.)"))
        else:
            self.status_var.set("Name removed.")

    def _voice_clip(self, data, label):
        """Where this speaker talks longest in the recording: {"file", "start", "end", "stream",
        "talk" (their total talking, seconds), "title"}. None if the recording isn't here."""
        segs = [x for x in data.get("segments", []) if x.get("speaker") == label]
        src = data.get("source")
        if not segs or not src or not os.path.exists(src):
            return None
        talk = sum(max(float(x["end"]) - float(x["start"]), 0) for x in segs)
        best = max(segs, key=lambda x: float(x["end"]) - float(x["start"]))
        offset, stream = 0.0, None
        item = self._shown_item() or {}
        if item.get("session"):
            from transcriber.session import read_session
            for f in (read_session(item["session"]) or {}).get("files") or []:
                if os.path.normcase(f.get("path", "")) == os.path.normcase(src):
                    offset = float(f.get("offset", 0))
        elif data.get("track"):
            stream = self._source_track_index(src, data["track"])
        a = float(best["start"]) - offset
        return {"file": src, "start": a, "end": min(float(best["end"]) - offset, a + 12), "stream": stream,
                "talk": talk, "title": item.get("key") or os.path.splitext(os.path.basename(src))[0]}

    def _learn_from_transcript(self, pid, voiceprint, clip):
        """Learn a voice named in a transcript, keeping a short clip of it (cut in the background)."""
        if clip is None:
            self.people.add_voice(pid, voiceprint, {"from": "A transcript"})
            return

        def work():
            from transcriber import recorder
            from transcriber.people import new_clip_path
            dest = new_clip_path()
            info = {"from": clip["title"]}
            try:
                talk = recorder.save_clip(clip["file"], clip["start"], clip["end"], clip["stream"], str(dest))
                info.update(audio=dest.name, seconds=round(talk, 1))
            except Exception:
                try:
                    dest.unlink()
                except OSError:
                    pass
            self.events.put(("call", lambda: self.people.add_voice(pid, voiceprint, info)))
        threading.Thread(target=work, daemon=True).start()

    def _rewrite_files_for(self, pid):
        """Update the names inside saved transcript files that mention this person."""
        from transcriber.edits import rewrite_from_speakers
        from transcriber.speakers import read_speakers_file
        folder = Path(self.out_var.get())
        try:
            candidates = list(folder.glob("*_speakers.json")) + list(folder.glob("*/*_speakers.json"))
        except OSError:
            return
        for sp in candidates:
            data = read_speakers_file(sp) or {}
            if any((info or {}).get("person") == pid for info in data.get("speakers", {}).values()):
                self._backup(self._files_for(sp))
                rewrite_from_speakers(sp, self._files_for(sp), self.people)
        self._refresh_saved(force=True)

    def _new_person_dialog(self, on_created=None, person=None):
        """Add a person (or edit one): name and an optional photo."""
        win = tk.Toplevel(self)
        win.title("Edit person" if person else "New person")
        win.transient(self)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=(22, 18))
        frame.pack(fill="both", expand=True)
        name_var = tk.StringVar(value=person["name"] if person else "")
        # path: a newly chosen picture; crop: (left, top, side) for it, or for the saved original
        photo = {"path": None, "crop": None, "recrop": False}
        big = max(int(72 * self.ui_scale), 60)
        preview = ttk.Label(frame)
        preview.grid(row=0, column=0, rowspan=3, padx=(0, 16))
        original = self.people.original_path(person["id"]) if person else None

        def refresh_preview():
            from PIL import Image, ImageOps, ImageTk
            from transcriber.people import _shape_mask, avatar_pil, default_crop
            img = None
            src = photo["path"] or (original if photo["recrop"] else None)
            if src:
                try:
                    pic = ImageOps.exif_transpose(Image.open(src)).convert("RGBA")
                    left, top, side = photo["crop"] or default_crop(pic.size)
                    img = pic.crop((round(left), round(top), round(left + side), round(top + side)))
                    img = img.resize((big, big), Image.LANCZOS)
                    img.putalpha(_shape_mask(big, self.avatars.shape))
                except Exception:
                    photo.update(path=None, crop=None, recrop=False)
                    img = None
            if img is None:
                stand_in = dict(person) if person else {"id": "new", "color": "#3b82f6", "photo": ""}
                stand_in["name"] = name_var.get() or "?"
                img = avatar_pil(stand_in, big, shape=self.avatars.shape)
            preview.image = ImageTk.PhotoImage(img)
            preview.configure(image=preview.image)

        ttk.Label(frame, text="Name").grid(row=0, column=1, sticky="w")
        entry = ttk.Entry(frame, textvariable=name_var, width=26)
        entry.grid(row=1, column=1, sticky="w", pady=(2, 8))
        name_var.trace_add("write", lambda *_: refresh_preview())

        def open_cropper(path, start=None, new_pick=False):
            from transcriber.cropper import CropDialog

            def done(box):
                if new_pick:
                    photo.update(path=path, crop=box, recrop=False)
                else:
                    photo.update(crop=box, recrop=photo["path"] is None)
                refresh_preview()
                adjust_btn.state(["!disabled"])
            try:
                CropDialog(win, str(path), self.avatars.shape, done, start=start,
                           ui_scale=self.ui_scale, dark=self.current_theme == "dark")
            except Exception:
                self.status_var.set("Couldn't open that picture.")

        def choose():
            path = filedialog.askopenfilename(parent=win, title="Choose a photo",
                                              filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"),
                                                         ("All files", "*.*")])
            if path:
                open_cropper(path, new_pick=True)

        def adjust():
            if photo["path"]:
                open_cropper(photo["path"], start=photo["crop"])
            elif original:
                open_cropper(original, start=photo["crop"] or (tuple(person["crop"]) if person.get("crop") else None))

        photo_row = ttk.Frame(frame)
        photo_row.grid(row=2, column=1, sticky="w")
        ttk.Button(photo_row, text="Choose photo...", command=choose).pack(side="left")
        adjust_btn = ttk.Button(photo_row, text="Adjust crop...", command=adjust)
        adjust_btn.pack(side="left", padx=6)
        if not original:
            adjust_btn.state(["disabled"])
        if person and person.get("photo"):
            def remove_photo():
                self.people.remove_photo(person["id"])
                person["photo"] = ""
                photo.update(path=None, crop=None, recrop=False)
                adjust_btn.state(["disabled"])
                self.avatars.clear()
                refresh_preview()
            ttk.Button(photo_row, text="Remove", command=remove_photo).pack(side="left")
        refresh_preview()
        words_var = tk.StringVar(value=(person or {}).get("words", ""))
        wrow = ttk.Frame(frame)
        wrow.grid(row=3, column=0, columnspan=2, sticky="w", pady=(14, 0))
        ttk.Label(wrow, text="Words they use").pack(side="left")
        info_icon(wrow, self, "Names, games, places or slang this person says, separated by commas. "
                              "Whisper uses them to spell things right, especially on this person's own mic.").pack(
            side="left", padx=4)
        ttk.Entry(frame, textvariable=words_var, width=44).grid(row=4, column=0, columnspan=2, sticky="w", pady=(2, 0))

        def save():
            name = name_var.get().strip()
            if not name:
                entry.focus_set()
                return
            if person:
                renamed = name != person["name"]
                self.people.rename(person["id"], name)
                pid = person["id"]
                if renamed:
                    self._rewrite_files_for(pid)
            else:
                pid = self.people.add(name)["id"]
            p = self.people.get(pid)
            if p is not None and p.get("words", "") != words_var.get().strip():
                p["words"] = words_var.get().strip()
                self.people.save()
            if photo["path"]:
                self.people.set_photo(pid, photo["path"], photo["crop"])
            elif photo["recrop"]:
                self.people.recrop(pid, photo["crop"])
            self.avatars.clear()
            win.destroy()
            if on_created:
                on_created(pid)
            else:
                self._redisplay()

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="Save", style="Accent.TButton", command=save).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 8))
        win.bind("<Return>", lambda e: save())
        win.bind("<Escape>", lambda e: win.destroy())
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_reqheight()) // 3
        win.geometry(f"+{x}+{y}")
        set_dark_titlebar(win, self.current_theme == "dark")
        win.grab_set()
        entry.focus_set()
        return win

    def _open_people(self):
        if getattr(self, "_people_win", None) and self._people_win.winfo_exists():
            self._people_win.lift()
            return
        win = tk.Toplevel(self)
        self._people_win = win
        win.title("People")
        win.transient(self)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=(22, 18))
        frame.pack(fill="both", expand=True)
        ph = ttk.Frame(frame)
        ph.pack(anchor="w", pady=(0, 10))
        ttk.Label(ph, text="People", style="Section.TLabel").pack(side="left")
        info_icon(ph, self, "Click a speaker's name in a transcript to say who it is, or record a voice sample "
                            "with the mic button. Each one helps the app recognize them next time."
                  ).pack(side="left", padx=6)
        listing = ttk.Frame(frame)
        listing.pack(fill="x")
        size = max(int(40 * self.ui_scale), 32)

        def rebuild():
            for w in listing.winfo_children():
                w.destroy()
            if not self.people.people:
                ttk.Label(listing, text="No one yet. Add someone, or name a speaker in a transcript.",
                          style="Muted.TLabel").pack(anchor="w", pady=6)
            for person in sorted(self.people.people, key=lambda q: q["name"].lower()):
                row = ttk.Frame(listing)
                row.pack(fill="x", pady=4)
                ttk.Label(row, image=self.avatars.get(person, size)).pack(side="left")
                info = ttk.Frame(row)
                info.pack(side="left", padx=12)
                ttk.Label(info, text=person["name"], font=ui_font(11, bold=True)).pack(anchor="w")
                n = len(person.get("voices", []))
                samples = ttk.Label(info, text=f"{n} voice sample{'s' if n != 1 else ''}" if n else
                                    "Voice not learned yet", style="Muted.TLabel", cursor="hand2" if n else "")
                samples.pack(anchor="w")
                if n:
                    samples.bind("<Button-1>", lambda e, q=person: self._voice_samples_dialog(q, on_change=rebuild))
                    samples.bind("<Enter>", lambda e, w=samples: w.configure(foreground=self._palette()["fg"]))
                    samples.bind("<Leave>", lambda e, w=samples: w.configure(foreground=""))
                    Tooltip(samples, "See, play or remove voice samples")
                icon_button(row, self, "delete", "Delete person",
                            lambda pid=person["id"]: remove(pid)).pack(side="right")
                icon_button(row, self, "mic", "Record a voice sample",
                            lambda q=person: self._record_voice_dialog(q, on_saved=rebuild)).pack(side="right", padx=(6, 0))
                ttk.Button(row, text="Samples", command=lambda q=person: self._voice_samples_dialog(
                    q, on_change=rebuild)).pack(side="right", padx=(6, 0))
                ttk.Button(row, text="Edit", command=lambda q=person: self._new_person_dialog(
                    on_created=lambda pid: (self._redisplay(), rebuild()), person=q)).pack(side="right", padx=6)

        def remove(pid):
            person = self.people.get(pid)
            if person and self._confirm(
                    title="Delete person?", heading=f"Delete {person['name']}?", body="", items=[],
                    detail="Their photo and voice samples are removed. Lines they were named on go back to "
                           "a numbered speaker.", confirm="Delete"):
                self.people.delete(pid)
                self._rewrite_files_for(pid)
                self.avatars.clear()
                rebuild()
                self._redisplay()

        rebuild()
        bottom = ttk.Frame(frame)
        bottom.pack(fill="x", pady=(14, 0))
        ttk.Button(bottom, text="Add person...", command=lambda: self._new_person_dialog(
            on_created=lambda pid: (rebuild(), self._redisplay()))).pack(side="left")
        ttk.Button(bottom, text="Colors...", command=self._people_colors_dialog).pack(side="left", padx=8)
        ttk.Button(bottom, text="Done", style="Accent.TButton", command=win.destroy).pack(side="right")
        win.bind("<Escape>", lambda e: win.destroy())
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + 70
        win.geometry(f"+{x}+{y}")
        set_dark_titlebar(win, self.current_theme == "dark")

    def _people_colors_dialog(self):
        """Each person's color (their name, captions, photo ring and talk-time bar): pick one of the
        app's colors or any color."""
        from tkinter import colorchooser
        from transcriber.people import PERSON_COLORS
        if getattr(self, "_colors_win", None) and self._colors_win.winfo_exists():
            self._colors_win.lift()
            return
        win = tk.Toplevel(self)
        self._colors_win = win
        win.title("People's colors")
        win.transient(self)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=(22, 18))
        frame.pack(fill="both", expand=True)
        head = ttk.Frame(frame)
        head.pack(anchor="w", pady=(0, 10))
        ttk.Label(head, text="People's colors", style="Section.TLabel").pack(side="left")
        info_icon(head, self, "Used for their name, their captions (Settings > Captions > Speaker color) "
                              "and their picture when there's no photo.").pack(side="left", padx=6)
        listing = ttk.Frame(frame)
        listing.pack(fill="x")
        size = max(int(32 * self.ui_scale), 26)
        sw = max(int(22 * self.ui_scale), 18)
        p = self._palette()

        def apply(pid, color):
            self.people.set_color(pid, color)
            self.avatars.clear()
            rebuild()
            self._redisplay()

        def custom(person):
            got = colorchooser.askcolor(color=person.get("color") or "#3b82f6", parent=win,
                                        title=f"{person['name']}'s color")
            if got and got[1]:
                apply(person["id"], got[1])

        def rebuild():
            for w in listing.winfo_children():
                w.destroy()
            if not self.people.people:
                ttk.Label(listing, text="No one yet. Add people in People.", style="Muted.TLabel").pack(anchor="w")
            for person in sorted(self.people.people, key=lambda q: q["name"].lower()):
                row = ttk.Frame(listing)
                row.pack(fill="x", pady=3)
                ttk.Label(row, image=self.avatars.get(person, size)).pack(side="left")
                ttk.Label(row, text=person["name"], width=14, font=ui_font(10, bold=True)).pack(side="left", padx=10)
                cur = (person.get("color") or "").lower()
                for c in PERSON_COLORS + ([cur] if cur and cur not in PERSON_COLORS else []):
                    picked = c == cur
                    s = tk.Canvas(row, width=sw, height=sw, highlightthickness=0, background=p["bg"], cursor="hand2")
                    s.create_oval(2, 2, sw - 2, sw - 2, fill=c, outline=p["fg"] if picked else c, width=2 if picked else 1)
                    if picked:
                        s.create_text(sw / 2, sw / 2, text="\u2713", fill="#ffffff", font=ui_font(8, bold=True))
                    s.bind("<Button-1>", lambda e, pid=person["id"], c=c: apply(pid, c))
                    s.pack(side="left", padx=2)
                ttk.Button(row, text="Other...", command=lambda q=person: custom(q)).pack(side="left", padx=(8, 0))

        rebuild()
        ttk.Button(frame, text="Done", style="Accent.TButton", command=win.destroy).pack(anchor="e", pady=(14, 0))
        win.bind("<Escape>", lambda e: win.destroy())
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_reqwidth()) // 2
        win.geometry(f"+{x}+{self.winfo_rooty() + 90}")
        set_dark_titlebar(win, self.current_theme == "dark")

    def _voice_samples_dialog(self, person, on_change=None):
        """A person's voice samples: where each came from, a play button where a clip was kept, and
        a warning on any that doesn't sound like the others (likely someone else, or silence)."""
        from transcriber import recorder
        from transcriber.people import ODD_ONE_OUT, VOICE_DIR
        pid = person["id"]
        win = tk.Toplevel(self)
        win.title(f"{person['name']}'s voice samples")
        win.transient(self)
        frame = ttk.Frame(win, padding=(22, 18))
        frame.pack(fill="both", expand=True)
        head = ttk.Frame(frame)
        head.pack(anchor="w", pady=(0, 10))
        ttk.Label(head, text=f"{person['name']}'s voice samples", style="Section.TLabel").pack(side="left")
        info_icon(head, self, "Each sample teaches the app this voice. Remove any that's someone else or "
                              "silence: a warning means it doesn't sound like the other samples.").pack(side="left", padx=6)
        p = self._palette()
        style = ttk.Style(self)
        style.configure("Voices.Treeview", rowheight=int(26 * self.ui_scale))
        cols = ("from", "added", "talk", "match")
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=8, selectmode="extended",
                            style="Voices.Treeview")
        for c, title, w in (("from", "From", 240), ("added", "Added", 180), ("talk", "Talking", 70),
                            ("match", "Sounds like them?", 130)):
            tree.heading(c, text=title, anchor="w")
            tree.column(c, width=int(w * self.ui_scale), anchor="w", stretch=c == "from")
        tree.tag_configure("odd", foreground=p["err"])
        tree.pack(fill="both", expand=True)
        note = tk.StringVar()
        ttk.Label(frame, textvariable=note, style="Muted.TLabel", wraplength=int(600 * self.ui_scale),
                  justify="left").pack(anchor="w", pady=(6, 0))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(12, 0))
        play_btn = ttk.Button(buttons, text="Play")
        play_btn.pack(side="left")
        del_btn = ttk.Button(buttons, text="Remove")
        del_btn.pack(side="left", padx=8)
        ttk.Button(buttons, text="Close", style="Accent.TButton", command=lambda: close()).pack(side="right")
        ttk.Button(buttons, text="Record another...", command=lambda: self._record_voice_dialog(
            self.people.get(pid) or person, on_saved=lambda: (fill(), on_change and on_change()))).pack(side="right", padx=8)
        state = {"stop": None}

        def when(text):
            try:
                import datetime
                d = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M")
                return d.strftime("%b %d, %Y %I:%M %p").replace(" 0", " ")
            except (TypeError, ValueError):
                return "Earlier"

        def fill():
            if not win.winfo_exists():
                return
            tree.delete(*tree.get_children())
            samples = self.people.voice_samples(pid)
            fits = self.people.voice_fit(pid)
            odd = 0
            for k, ((_vp, info), fit) in enumerate(zip(samples, fits)):
                bad = fit is not None and fit < ODD_ONE_OUT
                odd += bad
                match = "" if fit is None else "No" if bad else "Yes"
                talk = f"{info['seconds']:.0f} s" if info.get("seconds") else ""
                tree.insert("", "end", iid=str(k), values=(info.get("from") or "Earlier sample", when(info.get("added")),
                                                           talk, match), tags=("odd",) if bad else ())
            if not samples:
                note.set("No samples left. Record one, or name this person in a transcript.")
            elif odd:
                note.set("Play the ones in red: if it isn't them (or it's silent), remove it." if len(samples) > 2 else
                         "These two don't sound alike, so one may be someone else. Play them and remove the wrong "
                         "one, or record another so the app can tell which is off.")
            else:
                note.set("Older samples have no recording to play." if any(
                    not i.get("audio") for _v, i in samples) else "")
            update_buttons()

        def picked():
            return sorted(int(i) for i in tree.selection())

        def clip_of(k):
            samples = self.people.voice_samples(pid)
            name = samples[k][1].get("audio") if k < len(samples) else None
            path = VOICE_DIR / name if name else None
            return path if path and path.exists() else None

        def update_buttons(_e=None):
            ks = picked()
            play_btn.configure(state="normal" if len(ks) == 1 and clip_of(ks[0]) else "disabled")
            del_btn.configure(state="normal" if ks else "disabled")

        def stop_playing():
            if state["stop"]:
                try:
                    state["stop"]()
                except Exception:
                    pass
                state["stop"] = None

        def play(_e=None):
            ks = picked()
            path = clip_of(ks[0]) if len(ks) == 1 else None
            if not path:
                return
            stop_playing()
            try:
                state["stop"] = recorder.play_wav(str(path))
            except Exception as exc:
                note.set(f"Couldn't play it: {exc}")

        def remove(_e=None):
            ks = picked()
            if not ks:
                return
            stop_playing()
            for k in reversed(ks):
                self.people.remove_voice(pid, k)
            fill()
            if on_change:
                on_change()

        def close():
            stop_playing()
            win.destroy()

        play_btn.configure(command=play)
        del_btn.configure(command=remove)
        tree.bind("<<TreeviewSelect>>", update_buttons)
        tree.bind("<Double-1>", play)
        tree.bind("<Delete>", remove)
        win.bind("<Escape>", lambda e: close())
        win.protocol("WM_DELETE_WINDOW", close)
        fill()
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + 90
        win.geometry(f"+{x}+{y}")
        set_dark_titlebar(win, self.current_theme == "dark")

    def _record_voice_dialog(self, person, on_saved=None):
        """Record someone talking (mic, or PC audio for people on a call) and learn their voice."""
        from transcriber import recorder
        ok, msg = recorder.available()
        from transcriber.speakers import usable_token
        token = usable_token(self.hf_token.get())
        if ok and token is None:
            ok, msg = False, ("Voice samples use the speaker model. Add your Hugging Face token in "
                              "Settings > Speakers first.")
        if ok:
            ok, msg = detection_status()
        if not ok:
            missing = not recorder.available()[0] or not detection_status()[0]   # something to install
            if self._confirm(title="Record voice", heading="Can't record yet", body="", items=[],
                             detail=msg, confirm="What's installed..." if missing else "OK",
                             cancel="Close" if missing else None) and missing:
                self._open_setup("soundcard" if not recorder.available()[0] else "speakers")
            return
        try:
            devices = recorder.sources()
        except Exception as e:
            self._confirm(title="Record voice", heading="No audio devices found", body="", items=[],
                          detail=str(e), confirm="OK", cancel=None)
            return

        parent = getattr(self, "_people_win", None)
        parent = parent if parent is not None and parent.winfo_exists() else self
        win = tk.Toplevel(parent)
        win.title(f"Record {person['name']}'s voice")
        win.transient(parent)
        win.resizable(False, False)
        p = PALETTE[self.current_theme or "light"]
        frame = ttk.Frame(win, padding=(22, 18))
        frame.pack(fill="both", expand=True)
        head = ttk.Frame(frame)
        head.pack(fill="x")
        ttk.Label(head, image=self.avatars.get(person, max(int(40 * self.ui_scale), 32))).pack(side="left")
        ttk.Label(head, text=f"Record {person['name']}", style="Section.TLabel").pack(side="left", padx=10)
        info_icon(head, self, "Talk normally for 15 to 30 seconds. Only one person should be talking."
                  ).pack(side="left")
        ttk.Frame(frame, height=10).pack()

        # Where to listen
        src = ttk.Frame(frame)
        src.pack(fill="x")
        kind = tk.StringVar(value="mic")
        ttk.Label(src, text="Listen to").grid(row=0, column=0, sticky="w")
        radios = ttk.Frame(src)
        radios.grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Radiobutton(radios, text="Microphone", value="mic", variable=kind,
                        command=lambda: fill_devices()).pack(side="left")
        ttk.Radiobutton(radios, text="PC audio", value="pc", variable=kind,
                        command=lambda: fill_devices()).pack(side="left", padx=(12, 0))
        info_icon(radios, self, "Records whatever your PC is playing, like a friend on Discord or a video. "
                                "Mute other sounds while they talk.").pack(side="left", padx=4)
        ttk.Label(src, text="Device").grid(row=1, column=0, sticky="w", pady=(8, 0))
        device_var = tk.StringVar()
        device_box = ttk.Combobox(src, textvariable=device_var, state="readonly", width=44)
        device_box.grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(8, 0))

        def fill_devices():
            names = [n for _, n in devices[kind.get()]]
            device_box["values"] = names
            device_var.set(names[0] if names else "")



        # Optional sentences to read
        show_lines = tk.BooleanVar(value=True)
        lines_box = ttk.Frame(frame)
        top = ttk.Frame(frame)
        top.pack(fill="x", pady=(12, 0))
        ttk.Checkbutton(top, text="Show sentences to read", variable=show_lines,
                        command=lambda: toggle_lines()).pack(side="left")
        info_icon(top, self, "Optional. You can also just talk about anything.").pack(side="left", padx=4)
        shuffle_btn = ttk.Button(top, text="New sentences", command=lambda: set_lines())
        shuffle_btn.pack(side="right")
        lines_var = tk.StringVar()
        lines_label = tk.Label(lines_box, textvariable=lines_var, justify="left", anchor="w",
                               wraplength=int(440 * self.ui_scale), font=ui_font(11),
                               background=p["band"],
                               foreground=p["fg"], padx=14, pady=10)
        lines_label.pack(fill="x")

        def set_lines():
            lines_var.set("\n\n".join(recorder.pick_sentences(4)))

        def toggle_lines():
            if show_lines.get():
                lines_box.pack(fill="x", pady=(8, 0), after=top)
                shuffle_btn.pack(side="right")
            else:
                lines_box.pack_forget()
                shuffle_btn.pack_forget()

        set_lines()
        toggle_lines()

        # Level meter, timer and buttons
        meter_row = ttk.Frame(frame)
        meter_row.pack(fill="x", pady=(14, 0))
        meter = tk.Canvas(meter_row, height=8, highlightthickness=0, width=int(340 * self.ui_scale),
                          background=p["hover"])
        meter.pack(side="left", fill="x", expand=True)
        timer = tk.StringVar(value="0:00")
        ttk.Label(meter_row, textvariable=timer, width=6, anchor="e").pack(side="right")
        status = tk.StringVar(value="Press Record when ready.")
        ttk.Label(frame, textvariable=status, style="Muted.TLabel", wraplength=int(460 * self.ui_scale),
                  justify="left").pack(anchor="w", pady=(6, 0))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(14, 0))
        record_btn = ttk.Button(buttons, text="Record", style="Accent.TButton")
        record_btn.pack(side="right")
        close_btn = ttk.Button(buttons, text="Close")
        close_btn.pack(side="right", padx=(0, 8))
        play_btn = ttk.Button(buttons, text="Play")             # shown while reviewing a recording
        discard_btn = ttk.Button(buttons, text="Discard")
        state = {"rec": None, "busy": False, "pending": None, "stop_play": None, "length": 0.0}

        def tick():
            rec = state["rec"]
            if rec is None or not win.winfo_exists():
                return
            if rec.error:
                stop(failed=rec.error)
                return
            secs = rec.seconds()
            timer.set(f"{int(secs) // 60}:{int(secs) % 60:02d}")
            w = meter.winfo_width()
            meter.delete("all")
            meter.create_rectangle(0, 0, w * rec.level(), 8, width=0, fill="#3b82f6")
            state["loudest"] = max(state.get("loudest", 0.0), rec.level())
            if secs >= 3 and state["loudest"] < 0.02:
                status.set("Not hearing anything from this device. Stop, and pick another one above.")
            elif secs >= 15:
                status.set("That's enough for a good sample. Press Stop whenever you're done.")
            if secs >= 90:
                stop()
                return
            win.after(100, tick)

        def start():
            ids = devices[kind.get()]
            names = [n for _, n in ids]
            dev = ids[names.index(device_var.get())][0] if device_var.get() in names else None
            rec = recorder.Recorder(kind.get(), dev)
            state["rec"], state["loudest"] = rec, 0.0
            rec.start()
            record_btn.configure(text="Stop", command=stop)
            device_box.configure(state="disabled")
            status.set("Recording... talk normally.")
            tick()

        def stop(failed=None):
            rec = state["rec"]
            state["rec"] = None
            if rec is None:
                return
            rec.stop()
            meter.delete("all")
            device_box.configure(state="readonly")
            record_btn.configure(text="Record", command=start)
            if failed or rec.error:
                status.set(f"Recording failed: {failed or rec.error}")
                return
            if rec.seconds() < 4:
                status.set("That was too short. Record at least 5 seconds.")
                return
            peak, talk = rec.heard()
            problem = recorder.problem_with(peak, talk)
            if problem:                                 # silence makes a useless voiceprint: not kept
                status.set(f"Not saved. {problem}")
                return
            from transcriber.people import new_clip_path
            wav = str(new_clip_path())
            state["length"] = rec.save_wav(wav)
            state["talk"], state["pending"] = talk, wav
            play_btn.pack(side="left")
            discard_btn.pack(side="left", padx=8)
            record_btn.configure(text="Save", command=submit)
            device_box.configure(state="disabled")
            status.set("Listen back if you like, then Save it, or Discard and record again.")

        def stop_playing():
            if state["stop_play"]:
                try:
                    state["stop_play"]()
                except Exception:
                    pass
                state["stop_play"] = None
            if play_btn.winfo_exists():
                play_btn.configure(text="Play", command=play)

        def play():
            if not state["pending"]:
                return
            stop_playing()
            try:
                state["stop_play"] = recorder.play_wav(state["pending"])
            except Exception as exc:
                status.set(f"Couldn't play it: {exc}")
                return
            play_btn.configure(text="Stop", command=stop_playing)
            token_ = state["stop_play"]
            win.after(int(state["length"] * 1000) + 300,
                      lambda: state["stop_play"] is token_ and stop_playing())

        def end_review():
            stop_playing()
            state["pending"] = None
            play_btn.pack_forget()
            discard_btn.pack_forget()
            device_box.configure(state="readonly")
            record_btn.configure(text="Record", command=start)

        def discard():
            wav = state["pending"]
            end_review()
            if wav:
                try:
                    os.remove(wav)
                except OSError:
                    pass
            status.set("Discarded. Press Record to try again.")

        def submit():
            wav = state["pending"]
            if not wav:
                return
            end_review()
            state["busy"] = True
            record_btn.configure(state="disabled")
            status.set("Learning the voice...")

            def work():
                try:
                    vp = recorder.voiceprint_from_wav(wav, token,
                                                      log=lambda m: self.events.put(("call", lambda m=m: status.set(m))))
                    err = None
                except Exception as e:
                    vp, err = None, str(e) or type(e).__name__
                self.events.put(("call", lambda: finish(vp, err, wav)))

            threading.Thread(target=work, daemon=True).start()

        def finish(vp, err, wav):
            state["busy"] = False
            info = {"from": "Recorded (PC audio)" if kind.get() == "pc" else "Recorded (mic)",
                    "audio": os.path.basename(wav), "seconds": round(state.get("talk", 0.0), 1)}
            kept = not err and self.people.add_voice(person["id"], vp, info)
            if not kept:
                try:
                    os.remove(wav)
                except OSError:
                    pass
            if not win.winfo_exists():
                return
            record_btn.configure(state="normal")
            if not kept:
                status.set(f"Couldn't learn the voice: {err}" if err else
                           "Not saved: no voice could be read from that. Try again a little louder.")
                return
            n = len((self.people.get(person["id"]) or {}).get("voices", []))
            status.set(f"Saved. {person['name']} now has {n} voice sample{'s' if n != 1 else ''}. "
                       "Record again for better recognition, or Close.")
            if kind.get() == "mic" and show_lines.get():
                set_lines()
            if on_saved:
                on_saved()

        def close():
            if state["rec"] is not None:
                state["rec"].stop()
                state["rec"] = None
            if state["pending"]:                     # recorded but never saved: not kept
                discard()
            stop_playing()
            win.destroy()

        record_btn.configure(command=start)
        play_btn.configure(command=play)
        discard_btn.configure(command=discard)
        close_btn.configure(command=close)
        win.protocol("WM_DELETE_WINDOW", close)
        win.bind("<Escape>", lambda e: close())
        fill_devices()
        win.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + 40
        win.geometry(f"+{x}+{y}")
        set_dark_titlebar(win, self.current_theme == "dark")
        win.grab_set()

    def _view_menu(self, menu, line):
        """Lines / Paragraphs choices at the end of a row's right-click menu."""
        menu.add_separator()
        if self.transcript_view == "paragraphs":
            groups = getattr(self, "_group_lines", {})
            gl = next((g for g, info in groups.items() if info["open"] and info["key"] in self._expanded
                       and g <= line < g + len(info["metas"])), None)      # one text line per row when open
            if gl is not None:
                menu.add_command(label="Fold this paragraph", command=lambda: self._toggle_paragraph(gl, False))
            menu.add_command(label="Open all paragraphs", command=lambda: self._expand_all(True))
            menu.add_command(label="Fold all paragraphs", command=lambda: self._expand_all(False))
            menu.add_command(label="Show line by line", command=lambda: self._set_view("lines"))
        else:
            menu.add_command(label="Show as paragraphs", command=lambda: self._set_view("paragraphs"))
        on = bool(getattr(self, "ts_millis", False))
        menu.add_command(label="Timestamps without milliseconds" if on else "Timestamps with milliseconds",
                         command=lambda: self._set_ts_millis(not on))

    def _on_row_menu(self, event):
        idx = self.text.index(f"@{event.x},{event.y}")
        line = int(idx.split(".")[0])
        if "tsend" in self.text.tag_names(f"{line}.0"):          # a folded paragraph's second line
            line -= 1
        return self._row_menu(line, event.x_root, event.y_root)

    def _row_menu(self, line: int, x_root: int, y_root: int, short: bool = False, clip_range=None):
        """The right-click menu of a transcript row. short: the timeline's, just the everyday items
        (edit, who's speaking, mark, delete), plus Export clip when clip_range (the marked stretch) is given."""
        meta = self._row_meta.get(line)
        if not meta:
            if line in getattr(self, "_group_lines", {}):      # a folded paragraph
                p = self._palette()
                dark = self.current_theme == "dark"
                menu = tk.Menu(self, tearoff=0, background="#2b2b2b" if dark else "#ffffff", foreground=p["fg"],
                               activebackground=p["hover"], activeforeground=p["fg"], relief="flat", borderwidth=1,
                               font=ui_font(10))
                menu.add_command(label="Show its lines", command=lambda: self._toggle_paragraph(line, True))
                menu.add_command(label="Copy text", command=lambda: (
                    self.clipboard_clear(), self.clipboard_append(" ".join(m["orig"] for m in self._group_lines[line]["metas"]))))
                self._view_menu(menu, line)
                self._set_hover(line)
                try:
                    menu.tk_popup(x_root, y_root)
                finally:
                    menu.grab_release()
            return "break"
        if self._editing is not None and self._editing != line:
            self._end_edit()
        p = self._palette()
        dark = self.current_theme == "dark"
        colors = dict(background="#2b2b2b" if dark else "#ffffff", foreground=p["fg"],
                      activebackground=p["hover"], activeforeground=p["fg"], disabledforeground=p["muted"],
                      relief="flat", borderwidth=1, font=ui_font(10))
        menu = tk.Menu(self, tearoff=0, **colors)
        deleted = self._is_deleted(meta)
        menu.add_command(label="Edit text", state="disabled" if deleted else "normal",
                         command=lambda: self._start_edit(line, f"b{line}e"))
        files = meta["files"]
        if files.get("speakers") and not deleted:
            from transcriber.speakers import display_names, read_speakers_file
            data = read_speakers_file(files["speakers"]) or {}
            segs = data.get("segments", [])
            if 0 <= meta["i"] < len(segs):
                current = segs[meta["i"]].get("speaker")
                spk = data.get("speakers") or {}
                cur_person = (spk.get(current) or {}).get("person")
                names = display_names(data, self.people)
                who = tk.Menu(menu, tearoff=0, **colors)
                # the people in this transcript first (with their pictures); everyone else under Other
                in_use = self._people_in_use()
                size = max(int(18 * self.ui_scale), 16)
                self._menu_images = []                           # (Tk drops images nobody holds)

                def add_person(m, person):
                    img = self.avatars.get(person, size, person["name"], person["color"])
                    self._menu_images.append(img)
                    m.add_command(label="  " + person["name"] + ("   \u2713" if person["id"] == cur_person else ""),
                                  image=img, compound="left",
                                  command=lambda pid=person["id"]: self._assign_line(files, meta["i"], pid=pid))
                for pid in in_use:
                    person = self.people.get(pid)
                    if person:
                        add_person(who, person)
                others = [lab for lab in names if lab != current
                          and not self.people.get((spk.get(lab) or {}).get("person"))]
                for lab in others:                                # speakers here with no person yet
                    img = self.avatars.get(None, size, names[lab], "#64748b")
                    self._menu_images.append(img)
                    who.add_command(label="  " + names[lab], image=img, compound="left",
                                    command=lambda lab=lab: self._assign_line(files, meta["i"], label=lab))
                rest = [q for q in sorted(self.people.people, key=lambda q: q["name"].lower()) if q["id"] not in in_use]
                if rest:
                    if in_use or others:
                        who.add_separator()
                    other = tk.Menu(who, tearoff=0, **colors)
                    for person in rest:
                        add_person(other, person)
                    who.add_cascade(label="Other", menu=other)
                who.add_separator()
                who.add_command(label="New person...", command=lambda: self._new_person_dialog(
                    lambda pid: self._assign_line(files, meta["i"], pid=pid)))
                menu.add_cascade(label="Change Speaker" if current else "Set Speaker", menu=who)
        marked = line in getattr(self, "_marker_rows", {})
        sel = len(self._selected_rows(line)) > 1 or bool(self.text.tag_ranges("sel"))
        menu.add_command(label="Remove mark" if marked and not sel else ("Mark selection" if sel else "Mark"),
                         accelerator="M", command=(lambda: self._mark_selection()) if sel else
                         (lambda: self._toggle_mark(line)))
        if short:
            if clip_range:
                menu.add_command(label="Export clip...",
                                 command=lambda: self._clip_dialog(line, start=clip_range[0], end=clip_range[1]))
            menu.add_separator()
            menu.add_command(label="Restore line" if deleted else "Delete line",
                             command=lambda: self._toggle_delete(line))
            self._set_hover(line)
            try:
                menu.tk_popup(x_root, y_root)
            finally:
                menu.grab_release()
            return "break"
        suspects = getattr(self, "_suspect_rows", {})
        if line in suspects and not deleted:
            menu.add_command(label="Remove made-up words", command=lambda: self._remove_madeup([line]))
        if len(suspects) > 1:
            menu.add_command(label=f"Remove all made-up words ({len(suspects)} lines)",
                             command=lambda: self._remove_madeup(list(suspects)))
        n_rows = len(self._selected_rows(line))
        redo = tk.Menu(menu, tearoff=0, **colors)
        from transcriber.core import MODELS
        for m in MODELS:
            redo.add_command(label=m + ("   (most accurate)" if m == "large" else ""),
                             command=lambda m=m: self._redo_lines(line, m))
        menu.add_cascade(label=f"Redo {'these ' + str(n_rows) + ' lines' if n_rows > 1 else 'this line'} with",
                         menu=redo)
        menu.add_command(label="Export clip...", command=lambda: self._clip_dialog(line))
        # Focus: hear one person louder than everyone else
        speaker = self._row_speaker(meta)
        track = meta.get("track") or ""
        if self.player.focus:
            menu.add_command(label=f"Stop focusing on {self.player.focus['name']}",
                             command=lambda: self.player.set_focus(None))
        elif self.player.path:
            if speaker:
                menu.add_command(label=f"Focus on {speaker}", command=lambda: self._focus_on(speaker, track))
            if track and track != speaker:
                menu.add_command(label=f"Focus on the {track} track", command=lambda: self._focus_on(track, track))
        menu.add_command(label="Copy text", command=lambda: (
            self.clipboard_clear(), self.clipboard_append(self.text.get(f"b{line}s", f"b{line}e"))))
        menu.add_separator()
        menu.add_command(label="Restore line" if deleted else "Delete line",
                         command=lambda: self._toggle_delete(line))
        self._view_menu(menu, line)
        self._set_hover(line)
        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()
        return "break"

    def _people_in_use(self) -> list:
        """The people assigned somewhere in the shown transcript (every part), in the order they
        first appear."""
        out, seen_files = [], set()
        for meta in self._all_row_metas():
            files = meta.get("files") or {}
            fkey = str(files.get("speakers") or "")
            if not fkey or fkey in seen_files:
                continue
            seen_files.add(fkey)
            try:
                _segs, data = self._tl_segments(files)
            except Exception:
                continue
            for lab, info in ((data or {}).get("speakers") or {}).items():
                pid = (info or {}).get("person")
                if pid and pid not in out and self.people.get(pid):
                    out.append(pid)
        return out

    def _assign_line(self, files, i, pid=None, label=None):
        """Fix who's speaking on one line only (that speaker's other lines stay as they are)."""
        import json
        from transcriber.edits import rewrite_from_speakers
        from transcriber.speakers import read_speakers_file
        data = read_speakers_file(files["speakers"])
        if not data or not (0 <= i < len(data.get("segments", []))):
            return
        speakers = data.setdefault("speakers", {})
        if pid is not None:
            label = next((lab for lab, info in speakers.items() if (info or {}).get("person") == pid), None)
            if label is None:                          # this person isn't in the recording yet
                label = f"PERSON_{pid}"
                speakers[label] = {"person": pid, "voiceprint": None}
        if not label:
            return
        data["segments"][i]["speaker"] = label
        self._backup(files)
        write_text_safe(files["speakers"], json.dumps(data))
        rewrite_from_speakers(files["speakers"], files, self.people)
        person = self.people.get(pid) if pid else None
        self.status_var.set(f"That line is now {person['name']}." if person else "Speaker changed for that line.")
        self._redisplay()
        self._refresh_saved(force=True)

    def _row_speaker(self, meta) -> str:
        """The name shown on a row (from the captions, which hold the same lines)."""
        for cap in self.player.captions() or []:
            if len(cap) > 3 and cap[1] == meta["orig"] and int(cap[0]) == int(meta.get("sec", -1)):
                return cap[2] or ""
        return ""

    def _focus_on(self, name: str, track_label: str = ""):
        """Their own track (or mic) loud and the rest quiet; if they share a track with others,
        full volume only while they're talking."""
        own = next((t["n"] for t in self.player.tracks if t["name"] and t["name"] in (name, track_label)
                    and (t["name"] == name or not any(c[2] for c in self.player.captions() or []
                                                      if len(c) > 4 and c[4] == track_label and c[2] != name))),
                   None)
        spans = []
        if own is None:
            caps = sorted(self.player.captions() or [], key=lambda c: c[0])
            for k, cap in enumerate(caps):
                if len(cap) > 3 and cap[2] == name:
                    nxt = caps[k + 1][0] if k + 1 < len(caps) else cap[0] + 10
                    spans.append((cap[0] - 0.2, min(nxt, cap[0] + 15)))
            if not spans:
                self.bell()
                return
        self.player.set_focus(name, own, spans)
        self.status_var.set(f"Focusing on {name}. Everyone else is quieter. Click \u25ce {name} in the player to stop.")
