"""The done view's "Kapitler og beskrivelse" card: the short description Claude wrote, the chapters (time and title),
and "Kopiér kapitler" / "Kopiér beskrivelse" buttons, so the user can paste them into YouTube or an e-mail. The same
text is in "<video> - kapitler.txt" (chapters.py), which the hint names.

As the design system's mockup chapters-done.png and its spec (assets/ui/README.md, "New features (T8)", item 7):
the card replaces the compact step list in the right column (the step list only shows after an error); "FRA AI"
in Press Start 2P 10 px hint on the right of the header; the description in text; 28 px chapter rows with the time in
VT323 20 px heading in a 70 px column; 40 px copy buttons that read "Kopieret ✓" for 2 s; a hint naming the file.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from . import skin
from .chapters import Outline, chapters_text, fmt_time
from .i18n import t
from .theme import IMG, P, PIXEL_FONT, TERMINAL_FONT

MAX_ROWS = 8          # chapters shown; the rest are summed up in one line (all of them are copied)
MAX_SUMMARY = 420     # characters of the description shown (all of it is copied)
COPIED_MS = 2000


class ChaptersCard(skin.Card):
    def __init__(self, parent, scale: float, padding):
        super().__init__(parent, text=t("chapters.title"), padding=padding)
        self.k = k = scale
        self.kd = 2 if IMG.two else 1
        self.outline = Outline()
        self.columnconfigure(0, weight=1)
        # "FRA AI" on the right of the header, on the title's line (placed like the card's own title)
        left, top, right, _ = self._pad
        self.source = tk.Label(self, text=t("chapters.from_claude").upper(), background=P.card, foreground=P.hint,
                               borderwidth=0, padx=0, pady=0, font=(PIXEL_FONT, -10 * self.kd))
        self.source.place(relx=1.0, x=-right, y=top + max(0, (self.title.winfo_reqheight()
                                                              - self.source.winfo_reqheight()) // 2),
                          anchor="ne", bordermode="outside")
        self.summary = tk.Label(self, text="", background=P.card, foreground=P.text, borderwidth=0, padx=0, pady=0,
                                font="SunValleyBodyFont", anchor="w", justify="left")
        self.summary.grid(row=0, column=0, sticky="ew")
        self.rows = tk.Frame(self, background=P.card, borderwidth=0, highlightthickness=0)
        self.rows.grid(row=1, column=0, sticky="ew", pady=(int(12 * k), 0))
        self.rows.columnconfigure(0, minsize=int(70 * k))
        self.rows.columnconfigure(1, weight=1)
        self.acts = ttk.Frame(self)
        self.acts.grid(row=2, column=0, sticky="w", pady=(int(14 * k), 0))
        self.copy_ch = skin.IconButton(self.acts, "copy", text=t("chapters.copy_chapters"),
                                       command=lambda: self._copy(self.copy_ch, "chapters.copy_chapters",
                                                                  chapters_text(self.outline)))
        self.copy_sum = skin.IconButton(self.acts, "copy", text=t("chapters.copy_summary"),
                                        command=lambda: self._copy(self.copy_sum, "chapters.copy_summary",
                                                                   self.outline.summary))
        self.hint = ttk.Label(self, text="", style="Hint.TLabel", justify="left")
        self.hint.grid(row=3, column=0, sticky="ew", pady=(int(10 * k), 0))
        self.bind("<Configure>", self._wrap, add="+")

    def _wrap(self, e=None):
        width = max(200, self.winfo_width() - int(50 * self.k))
        self.summary.configure(wraplength=width)
        self.hint.configure(wraplength=width)
        for child in self.rows.grid_slaves(column=1):
            child.configure(wraplength=max(120, width - int(70 * self.k)))

    def show(self, outline: Outline | None, file: Path | None) -> bool:
        """Fill the card, or hide it when there is nothing (no chapters asked for, none in the answer, an error).
        Returns whether it is shown."""
        self.outline = outline or Outline()
        if not self.outline:
            self.grid_remove()
            return False
        k = self.k
        text = self.outline.summary
        if len(text) > MAX_SUMMARY:
            text = text[:MAX_SUMMARY].rsplit(" ", 1)[0] + " ..."
        self.summary.configure(text=text)
        self.summary.grid() if text else self.summary.grid_remove()
        for child in self.rows.winfo_children():
            child.destroy()
        chs = self.outline.chapters
        hours = bool(chs) and chs[-1].start >= 3_600_000
        for i, c in enumerate(chs[:MAX_ROWS]):
            self.rows.rowconfigure(i, minsize=int(28 * k))
            tk.Label(self.rows, text=fmt_time(c.start, hours), background=P.card, foreground=P.heading, borderwidth=0,
                     padx=0, pady=0, font=(TERMINAL_FONT, -20 * self.kd), anchor="w").grid(row=i, column=0, sticky="w")
            tk.Label(self.rows, text=c.title, background=P.card, foreground=P.text, borderwidth=0, padx=0, pady=0,
                     font="SunValleyBodyFont", anchor="w", justify="left").grid(row=i, column=1, sticky="ew")
        if len(chs) > MAX_ROWS:
            self.rows.rowconfigure(MAX_ROWS, minsize=int(28 * k))
            tk.Label(self.rows, text=t("chapters.more", n=len(chs) - MAX_ROWS), background=P.card, foreground=P.hint,
                     borderwidth=0, padx=0, pady=0, font="SunValleyBodyFont", anchor="w").grid(
                row=MAX_ROWS, column=1, sticky="w")
        if not chs:
            tk.Label(self.rows, text=t("chapters.no_chapters"), background=P.card, foreground=P.hint, borderwidth=0,
                     padx=0, pady=0, font="SunValleyBodyFont", anchor="w").grid(row=0, column=0, columnspan=2,
                                                                                 sticky="w")
        # Only the buttons that have something to copy
        self.copy_ch.configure(text=t("chapters.copy_chapters"))
        self.copy_sum.configure(text=t("chapters.copy_summary"))
        self.copy_ch.pack_forget()
        self.copy_sum.pack_forget()
        if chs:
            self.copy_ch.pack(side="left", padx=(0, int(14 * k)))
        if self.outline.summary:
            self.copy_sum.pack(side="left")
        self.hint.configure(text=t("chapters.hint", file=file.name) if file else "")
        self.hint.grid() if file else self.hint.grid_remove()
        self.grid()
        self.after_idle(self._wrap)
        return True

    def _copy(self, button, key: str, text: str):
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        button.configure(text=t("chapters.copied"))
        self.after(COPIED_MS, lambda: button.winfo_exists() and button.configure(text=t(key)))
