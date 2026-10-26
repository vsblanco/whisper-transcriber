"""The search box: filtering the list and finding lines in every transcript.

A mixin for the App class in app.py."""

from __future__ import annotations

import os
import tkinter as tk

from transcriber.core import format_timestamp
from transcriber.speakers import rows_for
from transcriber.ui_common import PALETTE


class SearchMixin:

    def _search_text(self) -> str:
        """What the user typed (empty while the gray hint is showing)."""
        return "" if self._search_placeholder else self.search_var.get()

    def _search_focus(self, focused: bool):
        if focused and self._search_placeholder:
            self._search_placeholder = False
            self.search_var.set("")
            self.search_entry.configure(foreground="")      # back to the theme's normal text color
        elif not focused and not self.search_var.get():
            self._search_placeholder = True
            self.search_var.set(self.SEARCH_HINT)
            self.search_entry.configure(foreground=PALETTE[self.current_theme or "light"]["muted"])

    def _search_changed(self):
        """Typing in the search box: filter the list and show matching lines (after a short pause)."""
        if self._search_placeholder:
            return
        if getattr(self, "_search_job", None):
            self.after_cancel(self._search_job)

        def run():
            self._search_job = None
            self._search_dirty = True
            self._refresh_saved(force=True)
            if not self._search_text().strip() and self.display and self.display[0] == "search":
                sel = self.saved.selection()
                self._show_saved(sel[0] if sel else None)
        self._search_job = self.after(250, run)

    def _content_rows(self, tr) -> list:
        """Every line of a transcript (latest version of each track): [(seconds, name, text)], cached."""
        paths = []
        for versions in tr["tracks"].values():
            v = versions[-1]
            f = v["files"].get("speakers") or v["files"].get("txt") or v["files"].get("srt") or v["files"].get("vtt")
            if f:
                paths.append(v["files"])
        key = tuple(sorted(str(f.get("speakers") or f.get("txt") or f.get("srt") or f.get("vtt")) for f in paths))
        try:
            stamp = tuple(os.path.getmtime(k) for k in key)
        except OSError:
            stamp = None
        cache = self.__dict__.setdefault("_content_cache", {})
        hit = cache.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
        rows = []
        for files in paths:
            rows += [(r[0], r[2], r[3]) for r in rows_for(files, self.people)]
        rows.sort(key=lambda r: r[0])
        cache[key] = (stamp, rows)
        return rows

    def _show_search_results(self, needle: str, shown: list):
        self.display = ("search", needle)
        self.player.load(None)
        self._search_hits = {}
        entries, count, limit = [], 0, 400
        iids = {it["key"]: iid for iid, it in self.saved_items.items()}
        for tr in shown:
            hits = [r for r in self._content_rows(tr) if needle in r[2].lower()]
            if not hits:
                continue
            entries.append((f"{tr['key']}   ", "trackhead"))
            entries.append((f"{len(hits)} match{'es' if len(hits) != 1 else ''}\n", "info"))
            for sec, name, text in hits:
                if count >= limit:
                    break
                entries.append((f"[{format_timestamp(sec)}]  ", "ts"))
                if name:
                    entries.append((f"{name}:  ", "spk0"))
                entries.append((text + "\n", "hit", None, ("search", iids.get(tr["key"]), sec, text)))
                count += 1
        if not entries:
            entries = [(f"Nothing found for \u201c{needle}\u201d.\n", "info")]
        elif count >= limit:
            entries.append((f"Showing the first {limit} matches.\n", "info"))
        self._render(f"Search: \u201c{needle}\u201d", entries, scroll_end=False)
        self._search_hits = {line: who[1:] for line, who in self._who_by_line.items() if who and who[0] == "search"}
        self._who_by_line = {}
        # Highlight the matched words.
        t = self.text
        t.tag_remove("find", "1.0", "end")
        start, n = "1.0", tk.IntVar()
        while True:
            pos = t.search(needle, start, stopindex="end", nocase=True, count=n)
            if not pos or not n.get():
                break
            t.tag_add("find", pos, f"{pos} + {n.get()} chars")
            start = f"{pos} + {n.get()} chars"
        t.tag_raise("find")

    def _jump_to_hit(self, iid, sec, text):
        """Open the transcript a search result is in and go to that line."""
        if not iid or iid not in self.saved_items:
            return
        self.saved.selection_set(iid)
        self.saved.see(iid)
        self._show_saved(iid)
        self.text.tag_remove("find", "1.0", "end")

        def land():
            for line, meta in self._row_meta.items():
                if meta.get("orig") == text and int(meta.get("sec", -1)) == int(sec):
                    self._set_now(line)
                    self._set_hover(line)
                    self.text.see(f"{line}.0")
                    self.player.seek(sec, play=False)
                    return
        self.after(60, land)
