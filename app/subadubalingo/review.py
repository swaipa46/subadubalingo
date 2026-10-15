"""The cut review: a timeline (loudness, speech, cuts) where the user drags the edges of the cuts, draws new ones and
switches them off, a list of the cuts, and a preview of each (frames before/after, play the transition).

Shown in the left column of the main window when the run reaches the step where the user reviews the cuts. When Claude drives the
job (MCP), the list is mirrored to jobs/<id>/cuts.json, and what the user tells Claude in the chat
(godkend_klip) arrives as jobs/<id>/cut_decision.json.
"""
from __future__ import annotations

import bisect
import os
import queue
import shutil
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import numpy as np

from . import cutting, jobs, scrub, skin
from .common import Cue, Runner, log
from .cutting import Cut, fmt_clock, fmt_length
from .i18n import t
from .theme import IMG, P, PIXEL_FONT, TERMINAL_FONT


def kind_colour(kind: str, part: str) -> str:
    """The theme's colour for a cut kind: part 'border' (outline, handles, label) or 'fill'."""
    return getattr(P, f"cut_{kind}_{part}", P.hint)


def kind_label(kind: str) -> str:
    """A cut kind's name in the UI language."""
    return cutting.kind_label(kind)

EDGE_PX = 7
SCRUB_W = 168         # thumbnails for scrubbing, in design px wide: the details column's preview box
SCRUB_FRAME_MS = 40   # while dragging, a new picture at most this often
SCRUB_SOUND_MS = 120  # and a new snippet of sound at most this often,
SCRUB_MOVE_MS = 60    # once the edge has moved this far
MIN_VIEW_MS = 8_000
KINDS = tuple(cutting.KINDS.keys())   # the legend's order: start, pause, end, inappropriate, offtopic, unclear, manual


class CutReview(ttk.Frame):
    def __init__(self, master, app, video: Path, duration: int, cues: list[Cue], cuts: list[Cut],
                 env: np.ndarray | None, on_done, mcp_job: str | None = None, wav: Path | None = None):
        super().__init__(master)
        self.app, self.video, self.dur, self.cues = app, video, max(1, duration), cues
        self.cuts = sorted(cuts, key=lambda c: c.start)
        self.env = env
        self.on_done, self.mcp_job = on_done, mcp_job
        self.k = k = app.scale
        self.kd = 2 if IMG.two else 1   # the design's px (1x) on the pixel grid: doubled at 125 % and above
        self._chips: dict[str, tk.PhotoImage] = {}
        self.v0, self.v1 = 0, self.dur
        self.sel: Cut | None = None
        self.drag: dict | None = None
        self.hover_t: int | None = None
        self.runner = Runner()
        self.tmp = Path(tempfile.mkdtemp(prefix="sdl-review-"))
        self.q: queue.Queue = queue.Queue()
        # Scrubbing: the picture and sound at the edge being dragged (scrub.py makes them in the background)
        self.scrub = scrub.Scrubber(video, self.tmp, int(SCRUB_W * k), wav)
        self._scrub_one = scrub.Latest(self._scrub_frame_work)   # exact frames while a thumbnail is not made yet
        self._scrub_ms: int | None = None      # the time being dragged; None when not scrubbing
        self._scrub_job = self._scrub_sound_job = None
        self._scrub_frame_t = self._scrub_sound_t = 0.0
        self._scrub_shown: int | None = None
        self._scrub_sound_ms: int | None = None
        self._scrub_img: tk.PhotoImage | None = None
        self._img_gen = 0
        self._images: list = []
        self._dirty_snapshot = True
        self._decision_msg = ""
        self.starts = [c.start for c in cues]
        self.snaps = sorted({0, self.dur} | {c.end for c in cues} |
                            {max(cues[i - 1].end if i else 0, c.start - 200) for i, c in enumerate(cues)})
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)   # extra height goes to the timeline; the list is as tall as it needs
        self.rowconfigure(7, weight=0)
        self._build()
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.after(50, self._redraw)
        self.after(120, self._poll)

    # --- Layout ---------------------------------------------------------------------------

    def _build(self):
        k, kd = self.k, self.kd
        head = ttk.Frame(self)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        # A short heading: Press Start 2P 16 px in heading (design review round 1, item 22), uppercased at render
        # time in every language like the other pixel-font headings (round 2, item 17)
        ttk.Label(head, text=t("review.title").upper(), style="Section.TLabel", foreground=P.heading,
                  font=(PIXEL_FONT, -16 * kd)).grid(row=0, column=0, sticky="w")
        zoom = ttk.Frame(head)
        zoom.grid(row=0, column=2, sticky="e")
        skin.IconButton(zoom, "minus", width=0, command=lambda: self._zoom(1.6)).pack(side="left")
        skin.IconButton(zoom, "plus", width=0, command=lambda: self._zoom(1 / 1.6)).pack(side="left", padx=4)
        ttk.Button(zoom, text=t("review.zoom_all"), command=self._zoom_all).pack(side="left")
        self.summary = ttk.Label(self, text="", font="SunValleyBodyStrongFont", foreground=P.step_active)
        self.summary.grid(row=1, column=0, sticky="w", pady=(4, 0))
        n_claude = sum(1 for c in self.cuts if c.source == "claude")
        n_auto = sum(1 for c in self.cuts if c.source == "auto")
        found = t("review.found", claude=n_claude, auto=n_auto) if self.cuts else t("review.found_none")
        hint = ttk.Label(self, style="Hint.TLabel", justify="left", text=(
            found + " " + t("review.hint") + (" " + t("review.hint_mcp") if self.mcp_job else "")))
        hint.grid(row=2, column=0, sticky="ew", pady=(2, 8))
        self.app._wrap(hint, self, 10)

        # The timeline inside frames/timeline (item 18); the Canvas is the frame's inside
        tl = ttk.Frame(self, style="Timeline.TFrame", padding=(6 * kd, 6 * kd, 10 * kd, 10 * kd))
        tl.grid(row=3, column=0, sticky="nsew")
        tl.columnconfigure(0, weight=1)
        tl.rowconfigure(0, weight=1)
        # About 260 px for the whole timeline at a 1000 px tall window; it also takes any extra height (round 2,
        # item 15)
        self.canvas = tk.Canvas(tl, height=int(244 * k), background=P.timeline_bg, highlightthickness=0,
                                borderwidth=0, cursor="crosshair")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.xbar = ttk.Scrollbar(self, orient="horizontal", command=self._xview)
        self.xbar.grid(row=4, column=0, sticky="ew", pady=(2 * kd, 0))
        self.xbar.grid_remove()   # only while zoomed in (_redraw)
        c = self.canvas
        c.bind("<Configure>", lambda e: self._redraw())
        c.bind("<Motion>", self._on_motion)
        c.bind("<Leave>", lambda e: self._set_hover(None))
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Double-Button-1>", self._on_double)
        c.bind("<MouseWheel>", self._on_wheel)
        c.bind("<Shift-MouseWheel>", lambda e: self._pan(-e.delta / 120 * 0.15))
        c.bind("<space>", lambda e: self._toggle(self.sel))
        c.bind("<Delete>", lambda e: self._delete(self.sel))

        # Legend: the seven cut kinds, a 16 px square (fill, 3 px border) and the name, 18 px apart (item 19)
        legend = ttk.Frame(self)
        legend.grid(row=5, column=0, sticky="w", pady=(10 * kd, 0))
        for i, kind in enumerate(KINDS):
            ttk.Label(legend, image=self._chip(kind, 16, 3)).grid(
                row=0, column=2 * i, padx=((18 if i else 0) * kd, 6 * kd))
            ttk.Label(legend, text=kind_label(kind)).grid(row=0, column=2 * i + 1)
        self.hover = ttk.Label(self, text=" ", style="Hint.TLabel")
        self.hover.grid(row=6, column=0, sticky="ew", pady=(2 * kd, 6 * kd))

        mid = self.mid = ttk.Frame(self)
        self._mid_min = 0
        mid.grid(row=7, column=0, sticky="nsew")
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(0, weight=1)
        # The list in a card (frames/card), without headings; the tree column holds the kind's colour chip (item 21)
        card = skin.Card(mid, padding=(14 * kd, 16 * kd, 18 * kd, 18 * kd))
        card.grid(row=0, column=0, sticky="new")   # as tall as its rows, like the mockup
        card.columnconfigure(0, weight=1)
        card.rowconfigure(0, weight=1)
        ttk.Style(self).configure("Review.Treeview", background=P.card, fieldbackground=P.card, bordercolor=P.card,
                                  lightcolor=P.card, darkcolor=P.card, indent=0, indicatorsize=0,
                                  indicatormargins=0)   # no expand indicator before the chip (a flat list)
        cols = ("nr", "tid", "hvorfor")
        self.tree = ttk.Treeview(card, columns=cols, show="tree", selectmode="browse", height=self._tree_rows(),
                                 style="Review.Treeview")
        self.tree.column("#0", width=int(12 * kd + 22 * k), minwidth=int(12 * kd + 22 * k), stretch=False)
        for col, title, width, stretch in (("nr", "#", 36, False), ("tid", t("review.col_time"), 180, False),
                                           ("hvorfor", t("review.col_why"), 220, True)):
            self.tree.heading(col, text=title, anchor="w")
            self.tree.column(col, width=int(width * k), stretch=stretch, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree_sb = sb = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        sb.grid_remove()   # only when the cuts do not fit (_refresh_list)
        keys = ttk.Label(mid, style="Hint.TLabel", justify="left", text=t("review.keys"))
        keys.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.tag_configure("off", foreground=P.hint)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-Button-1>", self._on_tree_double)
        self.tree.bind("<space>", lambda e: self._toggle(self.sel))
        self.tree.bind("<Delete>", lambda e: self._delete(self.sel))

        det = ttk.Frame(mid, padding=(14, 0, 0, 0))
        det.grid(row=0, column=1, rowspan=2, sticky="nsew")
        self.d_title = ttk.Label(det, text="", font="SunValleyBodyStrongFont")
        self.d_title.grid(row=0, column=0, columnspan=2, sticky="w")
        self.d_text = ttk.Label(det, text=t("review.click_cut"), style="Hint.TLabel", justify="left",
                                wraplength=int(340 * k))
        self.d_text.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 6))
        # The two frames in fixed 168 x 94 boxes (round 2, item 15c): a taller picture is cropped, not grown
        fw, fh = int(SCRUB_W * self.k), int(94 * self.k)
        self._no_frame = tk.PhotoImage(master=self, width=fw, height=fh)   # holds the box (pixel units) until a frame comes
        box = dict(background=P.card, borderwidth=0, width=fw, height=fh, image=self._no_frame)
        self.img_a = tk.Label(det, **box)
        self.img_a.grid(row=2, column=0, sticky="nw")
        self.img_b = tk.Label(det, **box)
        self.img_b.grid(row=2, column=1, sticky="nw", padx=(6, 0))
        self.cap_a = ttk.Label(det, text="", style="Hint.TLabel")
        self.cap_a.grid(row=3, column=0, sticky="w")
        self.cap_b = ttk.Label(det, text="", style="Hint.TLabel")
        self.cap_b.grid(row=3, column=1, sticky="w", padx=(6, 0))
        acts = ttk.Frame(det)
        acts.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.b_trans = skin.IconButton(acts, "play", text=t("review.play_transition"), command=lambda: self._play("trans"))
        self.b_trans.grid(row=0, column=0, sticky="w")
        self.b_cut = skin.IconButton(acts, "play", text=t("review.play_cut"), command=lambda: self._play("cut"))
        self.b_cut.grid(row=0, column=1, sticky="w", padx=(6, 0))
        self.b_toggle = ttk.Button(acts, text=t("review.keep"), command=lambda: self._toggle(self.sel))
        self.b_toggle.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.b_delete = ttk.Button(acts, text=t("review.delete"), command=lambda: self._delete(self.sel))
        self.b_delete.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(6, 0))
        self.detail_btns = (self.b_trans, self.b_cut, self.b_toggle, self.b_delete)

        bottom = ttk.Frame(self)
        bottom.grid(row=8, column=0, sticky="ew", pady=(14, 0))
        self.b_ok = skin.IconButton(bottom, "cut", text=t("review.approve"), style="Main.Accent.TButton",
                                    command=self._approve)
        self.b_ok.pack(side="left")
        ttk.Button(bottom, text=t("review.skip"), command=self._skip).pack(side="left", padx=10)
        ttk.Button(bottom, text=t("review.cancel"), command=self.app._cancel).pack(side="left")
        self.b_hint = ttk.Label(bottom, text="", style="Hint.TLabel")
        self.b_hint.pack(side="left", padx=12)
        self._refresh_list()
        self._show_details()
        if self.cuts:   # start on the first cut, so the details and its buttons are there from the start
            self.after_idle(lambda: self.cuts and self.sel is None and self._select(self.cuts[0], scroll=False))

    def _tree_rows(self) -> int:
        return 3   # the list shows three rows and scrolls (round 2, item 15); the timeline gets the height

    def _text_w(self, text: str, font) -> int:
        if not hasattr(self, "_font"):
            from tkinter import font as tkfont
            self._font = tkfont.Font(root=self, font=font)
        return self._font.measure(text)

    def _chip(self, kind: str, size: int, border: int) -> tk.PhotoImage:
        """A square in a cut kind's colours (size and border in design px): cut_<kind>_border around cut_<kind>_fill,
        or all border colour when border is 0. Kind "off" is a kept cut, in hint. Kept referenced (Tk drops images
        Python no longer holds)."""
        key = f"{kind}-{size}-{border}"
        if key not in self._chips:
            n, b = size * self.kd, border * self.kd
            img = tk.PhotoImage(master=self, width=n, height=n)
            img.put(P.hint if kind == "off" else kind_colour(kind, "border"), to=(0, 0, n, n))
            if b and n > 2 * b:
                img.put(P.card if kind == "off" else kind_colour(kind, "fill"), to=(b, b, n - b, n - b))
            self._chips[key] = img
        return self._chips[key]

    # --- Geometry -------------------------------------------------------------------------

    def _width(self) -> int:
        return max(100, self.canvas.winfo_width() - 2 * self._m())

    def _m(self) -> int:
        return 10 * self.kd

    def x_of(self, t: float) -> float:
        return self._m() + (t - self.v0) / max(1, self.v1 - self.v0) * self._width()

    def t_of(self, x: float) -> int:
        t = self.v0 + (x - self._m()) / self._width() * (self.v1 - self.v0)
        return int(min(max(t, 0), self.dur))

    def _snap(self, t: int) -> int:
        px = (self.v1 - self.v0) / self._width()
        i = bisect.bisect_left(self.snaps, t)
        best = t
        for j in (i - 1, i):
            if 0 <= j < len(self.snaps) and abs(self.snaps[j] - t) <= EDGE_PX * px and abs(self.snaps[j] - t) < abs(best - t) + 1e9:
                if best == t or abs(self.snaps[j] - t) < abs(best - t):
                    best = self.snaps[j]
        return best

    # --- View -----------------------------------------------------------------------------

    def _set_view(self, v0: float, v1: float):
        span = min(self.dur, max(MIN_VIEW_MS, v1 - v0))
        v0 = min(max(0, v0), self.dur - span)
        self.v0, self.v1 = int(v0), int(v0 + span)
        self._redraw()

    def _zoom(self, factor: float, at: int | None = None):
        at = at if at is not None else (self.sel.start + self.sel.end) // 2 if self.sel else (self.v0 + self.v1) // 2
        span = (self.v1 - self.v0) * factor
        frac = (at - self.v0) / max(1, self.v1 - self.v0)
        self._set_view(at - span * frac, at - span * frac + span)

    def _zoom_all(self):
        self._set_view(0, self.dur)

    def _pan(self, frac: float):
        span = self.v1 - self.v0
        self._set_view(self.v0 + span * frac, self.v0 + span * frac + span)

    def _xview(self, *args):
        span = self.v1 - self.v0
        if args[0] == "moveto":
            v0 = float(args[1]) * self.dur
            self._set_view(v0, v0 + span)
        elif args[0] == "scroll":
            step = span * (0.1 if args[2] == "units" else 0.9) * int(args[1])
            self._set_view(self.v0 + step, self.v0 + step + span)

    def _show_cut(self, c: Cut):
        """Bring a cut into view with some context, zooming in on short cuts."""
        pad = max(10_000, (c.end - c.start) * 0.4)
        if c.start < self.v0 or c.end > self.v1 or (self.v1 - self.v0) > 12 * (c.end - c.start + 2 * pad):
            self._set_view(c.start - pad, c.end + pad)

    # --- Drawing --------------------------------------------------------------------------

    def _redraw(self):
        c = self.canvas
        if not c.winfo_exists():
            return
        c.delete("all")
        k, kd, m = self.k, self.kd, self._m()
        w, h = c.winfo_width(), c.winfo_height()
        ruler = 28 * kd                      # the ruler row, with a 2 px bottom border in card_border (item 18)
        top, bottom = ruler + 2 * kd, h - 2 * kd
        mid = (top + bottom) // 2
        amp = (bottom - top) / 2 - 14 * kd
        # cut fills first, under the sound
        for cut in self.cuts:
            if cut.on and cut.end > self.v0 and cut.start < self.v1:
                x0, x1 = max(m, self.x_of(cut.start)), min(w - m, self.x_of(cut.end))
                c.create_rectangle(x0, top, x1, bottom, fill=kind_colour(cut.kind, "fill"), outline="")
        # loudness
        if self.env is not None and len(self.env):
            cols = max(2, int(self._width() / max(1, k)))
            i0 = self.v0 / cutting.ENVELOPE_MS
            i1 = self.v1 / cutting.ENVELOPE_MS
            idx = np.clip(np.linspace(i0, i1, cols + 1)[:-1].astype(int), 0, len(self.env) - 1)
            vals = np.maximum.reduceat(self.env, idx) if len(idx) else np.zeros(0)
            level = np.clip((vals + 60) / 60, 0.02, 1.0) * amp
            xs = m + np.arange(cols) * self._width() / cols
            upper = [v for x, l in zip(xs, level) for v in (x, mid - l)]
            lower = [v for x, l in zip(xs[::-1], level[::-1]) for v in (x, mid + l)]
            if len(upper) >= 4:
                c.create_polygon(upper + lower, fill=P.timeline_waveform, outline="")
        # speech
        y0, y1 = bottom - 14 * kd, bottom - 8 * kd
        i = max(0, bisect.bisect_right(self.starts, self.v0) - 1)
        last_x = -10
        while i < len(self.cues) and self.cues[i].start < self.v1:
            q = self.cues[i]
            x0, x1 = self.x_of(q.start), self.x_of(q.end)
            if x1 >= m and x1 - last_x >= 1:
                c.create_rectangle(max(m, x0), y0, min(w - m, max(x1, x0 + 1)), y1, fill=P.timeline_speech, outline="")
                last_x = x1
            i += 1
        # cut outlines (3 px, 5 px selected; inside the cut), drag handles (10 x 44 px, centred on the edges) and
        # labels, in cut_<kind>_border (item 20)
        font = ("Segoe UI Semibold", 10)
        for n, cut in enumerate(self.cuts, 1):
            if cut.end <= self.v0 or cut.start >= self.v1:
                continue
            strong = kind_colour(cut.kind, "border") if cut.on else P.hint
            x0, x1 = round(self.x_of(cut.start)), round(self.x_of(cut.end))
            selected = cut is self.sel
            lw = (5 if selected else 3) * kd
            if x1 - x0 > lw:
                c.create_rectangle(x0 + lw / 2, top + lw / 2, x1 - lw / 2, bottom - lw / 2 + 1, outline=strong,
                                   width=lw, dash=() if cut.on else (4 * kd, 3 * kd))
            else:
                c.create_rectangle(x0, top, x0 + lw, bottom, outline="", fill=strong)
            if cut.on or selected:
                for x in (x0, x1):
                    c.create_rectangle(x - 5 * kd, mid - 22 * kd, x + 5 * kd, mid + 22 * kd, fill=strong, outline="")
            label = f"{n} {kind_label(cut.kind) if cut.on else t('review.kept')}"
            tx = max(x0, m) + lw + 6 * kd
            if x1 - tx > self._text_w(label, font) + 6 * kd:
                c.create_text(tx, top + lw + 6 * kd, text=label, anchor="nw", fill=strong, font=font)
            elif x1 - tx > self._text_w(str(n), font) + 2 * kd:
                c.create_text(tx, top + lw + 6 * kd, text=str(n), anchor="nw", fill=strong, font=font)
        # ruler: ticks and times in VT323 in timeline_ruler, over anything that reaches up there
        c.create_rectangle(0, 0, w, top, fill=P.timeline_bg, outline="")
        c.create_rectangle(m, ruler, w - m, top, fill=P.card_border, outline="")
        span_s = (self.v1 - self.v0) / 1000
        px_per_s = self._width() / max(0.001, span_s)
        step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600) if s * px_per_s >= 80 * k), 3600)
        sec = (self.v0 // 1000 // step) * step
        while sec * 1000 <= self.v1:
            x = round(self.x_of(sec * 1000))
            if m - 1 <= x <= w - m - 2 * kd:
                c.create_rectangle(x, 6 * kd, x + 2 * kd, ruler, fill=P.timeline_ruler, outline="")
                # VT323 18 px, 5 px after the 2 px tick (round 2, item 14)
                c.create_text(x + 7 * kd, ruler - 3 * kd, text=fmt_clock(sec * 1000), anchor="sw", fill=P.timeline_ruler,
                              font=(TERMINAL_FONT, -18 * kd))
            sec += step
        # hover line
        if self.hover_t is not None and self.v0 <= self.hover_t <= self.v1:
            x = self.x_of(self.hover_t)
            c.create_line(x, top, x, bottom, fill=P.heading)
        self.xbar.set(self.v0 / self.dur, self.v1 / self.dur)
        if self.v0 > 0 or self.v1 < self.dur:   # the scroll bar only while zoomed in
            self.xbar.grid()
        else:
            self.xbar.grid_remove()

    # --- Mouse ----------------------------------------------------------------------------

    def _hit(self, x: float):
        """('edge', cut, 'start'|'end') or ('cut', cut) or None. The selected cut wins."""
        order = ([self.sel] if self.sel else []) + [c for c in self.cuts if c is not self.sel]
        for cut in order:
            for side, at in (("start", cut.start), ("end", cut.end)):
                if abs(self.x_of(at) - x) <= EDGE_PX * self.k:
                    return ("edge", cut, side)
        for cut in order:
            if self.x_of(cut.start) <= x <= self.x_of(cut.end):
                return ("cut", cut)
        return None

    def _set_hover(self, ms: int | None):
        self.hover_t = ms
        if ms is None:
            self.hover.configure(text=" ")
        else:
            text = cutting.text_at(self.cues, ms)
            inside = next((c for c in self.cuts if c.start <= ms <= c.end), None)
            where = f"   [{kind_label(inside.kind) if inside.on else t('review.kept')}]" if inside else ""
            self.hover.configure(text=f"{fmt_clock(ms)}{where}   "
                                 + (t("review.quote", text=text) if text else t("review.no_speech")))
        self._redraw()

    def _on_motion(self, e):
        hit = self._hit(e.x)
        self.canvas.configure(cursor="sb_h_double_arrow" if hit and hit[0] == "edge" else
                              "hand2" if hit else "crosshair")
        self._set_hover(self.t_of(e.x))

    def _on_press(self, e):
        self.canvas.focus_set()
        hit = self._hit(e.x)
        t = self.t_of(e.x)
        if hit and hit[0] == "edge":
            self.drag = {"mode": "edge", "cut": hit[1], "side": hit[2], "x": e.x, "moved": False}
            self._select(hit[1], scroll=False)
        elif hit:
            self.drag = {"mode": "click", "cut": hit[1], "x": e.x, "moved": False}
        else:
            self.drag = {"mode": "new", "t0": t, "x": e.x, "cut": None, "moved": False}

    def _on_drag(self, e):
        d = self.drag
        if not d:
            return
        if abs(e.x - d["x"]) > 3:
            d["moved"] = True
        t = self._snap(self.t_of(e.x))
        if d["mode"] == "edge":
            cut = d["cut"]
            if d["side"] == "start":
                cut.start = min(t, cut.end - cutting.MIN_CUT_MS)
            else:
                cut.end = max(t, cut.start + cutting.MIN_CUT_MS)
            if cut.source == "auto":
                cut.source = "user"
            self._scrub_to(cut.start if d["side"] == "start" else cut.end)
        elif d["mode"] == "new" and d["moved"]:
            a, b = sorted((self._snap(d["t0"]), t))
            if d["cut"] is None:
                d["cut"] = Cut(a, max(b, a + 1), "manual", kind_label("manual"), True, "user")
                self.cuts.append(d["cut"])
                self.sel = d["cut"]
            d["cut"].start, d["cut"].end = a, max(b, a + 1)
            self._scrub_to(t)   # the moving end
        self._set_hover(self.t_of(e.x))

    def _on_release(self, e):
        d, self.drag = self.drag, None
        self._hide_scrub()
        if not d:
            return
        if d["mode"] == "new":
            cut = d["cut"]
            if cut is not None and cut.end - cut.start < 1000:
                self.cuts.remove(cut)
                cut = None
            if cut is None:
                self.sel = None
                self._show_frame_at(self.t_of(e.x))
            self._changed(select=cut)
        elif d["mode"] == "edge":
            self._changed(select=d["cut"])
        else:
            self._select(d["cut"], scroll=False)

    def _on_double(self, e):
        hit = self._hit(e.x)
        if hit and hit[0] == "cut":
            self._toggle(hit[1])

    def _on_wheel(self, e):
        self._zoom(0.8 if e.delta > 0 else 1.25, at=self.t_of(e.x))

    # --- List -----------------------------------------------------------------------------

    def _refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        self.tree.configure(height=self._tree_rows())
        for n, cut in enumerate(self.cuts, 1):
            why = kind_label(cut.kind) + (f": {cut.reason}" if cut.reason else "")
            self.tree.insert("", "end", iid=str(n), tags=() if cut.on else ("off",),
                             image=self._chip(cut.kind if cut.on else "off", 12, 0), values=(
                f"{n}" if cut.on else f"({n})", f"{fmt_clock(cut.start)} - {fmt_clock(cut.end)} ({fmt_length(cut.length)})",
                why if cut.on else t("review.list_kept", why=why)))
        if len(self.cuts) > self._tree_rows():
            self.tree_sb.grid()
        else:
            self.tree_sb.grid_remove()
        if self.sel in self.cuts:
            iid = str(self.cuts.index(self.sel) + 1)
            self.tree.selection_set(iid)
            self.tree.see(iid)
        ranges = cutting.active(self.cuts, self.dur)
        removed = cutting.removed_ms(ranges)
        n_on = sum(1 for c in self.cuts if c.on)
        if n_on:
            one = "_one" if n_on == 1 else ""   # English needs "1 cut" / "2 cuts"
            self.summary.configure(text=t("review.summary" + one, n=n_on, removed=fmt_length(removed),
                                          new=fmt_clock(self.dur - removed), old=fmt_clock(self.dur)))
            self.b_ok.configure(text=t("review.approve_n" + one, n=n_on), state="normal")
            self.b_hint.configure(text="")
        else:
            self.summary.configure(text=t("review.summary_none", length=fmt_clock(self.dur)))
            self.b_ok.configure(text=t("review.approve"), state="disabled")
            self.b_hint.configure(text=t("review.skip_hint", skip=t("review.skip")))
        self._dirty_snapshot = True

    def _on_tree_select(self, _=None):
        sel = self.tree.selection()
        if sel:
            cut = self.cuts[int(sel[0]) - 1]
            if cut is not self.sel:
                self._select(cut)

    def _on_tree_double(self, e):
        if self.tree.identify_row(e.y):
            self._toggle(self.cuts[int(self.tree.identify_row(e.y)) - 1])

    # --- Changes --------------------------------------------------------------------------

    def _changed(self, select: Cut | None = None):
        self.cuts.sort(key=lambda c: c.start)
        if select is not None:
            self.sel = select
        self._refresh_list()
        self._show_details()
        self._redraw()

    def _select(self, cut: Cut | None, scroll: bool = True):
        self.sel = cut
        if cut and scroll:
            self._show_cut(cut)
        if cut in self.cuts:
            iid = str(self.cuts.index(cut) + 1)
            if self.tree.selection() != (iid,):
                self.tree.selection_set(iid)
            self.tree.see(iid)
        self._show_details()
        self._redraw()

    def _toggle(self, cut: Cut | None):
        if cut:
            cut.on = not cut.on
            self._changed(select=cut)

    def _delete(self, cut: Cut | None):
        if cut and cut in self.cuts:
            self.cuts.remove(cut)
            self.sel = None
            self._changed()

    # --- Details and previews ---------------------------------------------------------------

    def _show_details(self):
        cut = self.sel
        for b in self.detail_btns:
            b.configure(state="normal" if cut else "disabled")
        if not cut:
            self.d_title.configure(text="")
            self.d_text.configure(text=t("review.click_cut_or_empty"))
            return
        n = self.cuts.index(cut) + 1 if cut in self.cuts else 0
        self.d_title.configure(text=t("review.detail_title", n=n, kind=kind_label(cut.kind)), foreground=kind_colour(cut.kind, "border") if cut.on else P.hint)
        said = cutting.excerpt(self.cues, cut.start, cut.end, 110)   # short: the timeline needs the height
        text = ((cut.reason + "\n" if cut.reason else "")
                + f"{fmt_clock(cut.start)} - {fmt_clock(cut.end)} ({fmt_length(cut.length)})"
                + "\n" + (t("review.quote", text=said) if said else t("review.no_speech"))
                + ("" if cut.on else "\n" + t("review.kept_in_video")))
        self.d_text.configure(text=text + "\n" * max(0, 3 - text.count("\n")))   # about the same height for every cut
        self.after_idle(self._hold_height)
        self.b_toggle.configure(text=t("review.keep") if cut.on else t("review.cut_anyway"))
        self.cap_a.configure(text=t("review.just_before", time=fmt_clock(cut.start)))
        self.cap_b.configure(text=t("review.just_after", time=fmt_clock(cut.end)))
        self._load_frames([(max(0, cut.start - 400), self.img_a), (min(self.dur - 200, cut.end + 300), self.img_b)])

    def _hold_height(self):
        """Keep the list and details at the tallest height they have needed, so the timeline above does not jump
        up and down when another cut (with more text, or before its pictures arrive) is shown."""
        try:
            h = self.mid.winfo_reqheight()
        except tk.TclError:
            return
        if h > self._mid_min:
            self._mid_min = h
            self.rowconfigure(7, minsize=h)

    def _show_frame_at(self, ms: int):
        self.cap_a.configure(text=t("review.frame_at", time=fmt_clock(ms)))
        self.cap_b.configure(text="")
        self.img_b.configure(image=self._no_frame)
        self._load_frames([(ms, self.img_a)])

    def _load_frames(self, items):
        self._img_gen += 1
        gen = self._img_gen
        width = int(168 * self.k)

        def work():
            for ms, label in items:
                out = self.tmp / f"f{gen}-{ms}.png"
                try:
                    ok = cutting.frame_png(self.runner, self.video, ms, out, width)
                except Exception:
                    ok = False
                self.q.put(("img", gen, label, out if ok else None))
        threading.Thread(target=work, daemon=True).start()

    # --- Scrubbing (the picture and sound at the edge being dragged) ---------------------------

    def _scrub_to(self, ms: int):
        """The dragged edge is at ms: show its picture and play its sound soon, no more often than the limits."""
        if self._scrub_ms is None:
            self._img_gen += 1   # frames still coming for the cut's details would cover the live picture
        self._scrub_ms = ms
        now = time.monotonic()
        if self._scrub_job is None:
            wait = SCRUB_FRAME_MS / 1000 - (now - self._scrub_frame_t)
            self._scrub_job = self.after(max(0, int(wait * 1000)), self._scrub_frame_tick)
        if self._scrub_sound_job is None:
            wait = SCRUB_SOUND_MS / 1000 - (now - self._scrub_sound_t)
            self._scrub_sound_job = self.after(max(0, int(wait * 1000)), self._scrub_sound_tick)

    def _scrub_frame_tick(self):
        self._scrub_job = None
        ms = self._scrub_ms
        if ms is not None and ms != self._scrub_shown:
            self._scrub_shown, self._scrub_frame_t = ms, time.monotonic()
            self._show_scrub_frame(ms)

    def _scrub_sound_tick(self):
        self._scrub_sound_job = None
        ms = self._scrub_ms
        if ms is not None and (self._scrub_sound_ms is None or abs(ms - self._scrub_sound_ms) >= SCRUB_MOVE_MS):
            self._scrub_sound_ms, self._scrub_sound_t = ms, time.monotonic()
            self.scrub.play(ms)

    def _show_scrub_frame(self, ms: int):
        """Show the picture at ms (the nearest thumbnail, or an exact frame made now) with its time. The one place
        that decides where the live picture goes."""
        self.cap_a.configure(text=t("review.scrub_at", time=scrub.fmt_fine(ms)))
        path = self.scrub.thumb(ms)
        if path is None:
            self._scrub_one.put(ms)   # arrives as ("scrub", ms, path) in _poll
            return
        self._put_scrub_image(path)

    def _put_scrub_image(self, path: Path):
        try:
            self._scrub_img = tk.PhotoImage(master=self, file=str(path))   # kept: Tk drops images Python lets go
        except tk.TclError:
            return
        self.img_a.configure(image=self._scrub_img)

    def _scrub_frame_work(self, ms: int):
        if self.scrub.thumb(ms) is not None:   # the thumbnail came meanwhile
            self.q.put(("scrub", ms, self.scrub.thumb(ms)))
            return
        try:
            path = self.scrub.frame(self.runner, ms)
        except Exception:
            path = None
        self.q.put(("scrub", ms, path))

    def _hide_scrub(self):
        """Scrubbing ends (the mouse is let go): stop the sound. The picture stays until the release shows the
        cut's details (or the frame where the user clicked) as before."""
        for job in (self._scrub_job, self._scrub_sound_job):
            if job is not None:
                self.after_cancel(job)
        self._scrub_job = self._scrub_sound_job = None
        if self._scrub_ms is not None:
            self.scrub.silence()
        self._scrub_ms = self._scrub_shown = self._scrub_sound_ms = None

    def _play(self, what: str):
        cut = self.sel
        if not cut:
            return
        btn = self.b_trans if what == "trans" else self.b_cut
        old = btn.cget("text")
        btn.configure(text=t("review.making_preview"), state="disabled")
        out = self.tmp / f"{what}-{cut.start}-{cut.end}.mp4"

        def work():
            try:
                if what == "trans":
                    ok = cutting.preview_transition(self.runner, self.video, cut.start, cut.end, out)
                else:
                    ok = cutting.preview_cut(self.runner, self.video, cut.start, cut.end, out)
            except Exception:
                log.exception("preview failed")
                ok = False
            self.q.put(("play", btn, old, out if ok else None))
        threading.Thread(target=work, daemon=True).start()

    # --- Polling (frames, previews, Claude's decisions) ----------------------------------------

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                ev = self.q.get_nowait()
                if ev[0] == "img":
                    _, gen, label, path = ev
                    if gen == self._img_gen and path:
                        img = tk.PhotoImage(file=str(path))
                        self._images = [i for i in self._images if i is not getattr(label, "_img", None)] + [img]
                        label._img = img
                        label.configure(image=img)
                        self.after_idle(self._hold_height)
                elif ev[0] == "scrub":
                    _, ms, path = ev
                    # only while still dragging, and not when a newer picture is already shown
                    if path and self._scrub_ms is not None and ms == self._scrub_shown:
                        self._put_scrub_image(path)
                elif ev[0] == "play":
                    _, btn, old, path = ev
                    btn.configure(text=old, state="normal" if self.sel else "disabled")
                    if path:
                        os.startfile(path)
        except queue.Empty:
            pass
        except tk.TclError:
            pass
        if self.mcp_job:
            self._sync_mcp()
        self.after(150, self._poll)

    def snapshot(self) -> dict:
        ranges = cutting.active(self.cuts, self.dur)
        removed = cutting.removed_ms(ranges)
        return {"cuts": [dict(c.to_json(), text=cutting.excerpt(self.cues, c.start, c.end)) for c in self.cuts],
                "summary": f"Videoen bliver {fmt_clock(self.dur - removed)} lang (nu {fmt_clock(self.dur)}); "
                           f"{fmt_length(removed)} klippes væk.",
                "decision_message": self._decision_msg, "updated": time.time()}

    def _sync_mcp(self):
        d = jobs.root() / self.mcp_job
        decision = jobs.read_json(d / "cut_decision.json")
        if decision:
            self._apply_decision(decision)
            jobs.write_json(d / "cuts.json", self.snapshot())
            self._dirty_snapshot = False
            (d / "cut_decision.json").unlink(missing_ok=True)
            if decision.get("approve"):
                self._approve()
            return
        if self._dirty_snapshot and not self.drag:
            jobs.write_json(d / "cuts.json", self.snapshot())
            self._dirty_snapshot = False

    def _apply_decision(self, decision: dict):
        msgs = []
        keep = [n for n in decision.get("keep", []) if 1 <= n <= len(self.cuts)]
        for n in keep:
            self.cuts[n - 1].on = False
        if keep:
            msgs.append("Beholdes: klip " + ", ".join(str(n) for n in keep) + ".")
        bad_n = [n for n in decision.get("keep", []) if not 1 <= n <= len(self.cuts)]
        if bad_n:
            msgs.append("Findes ikke: klip " + ", ".join(str(n) for n in bad_n) + ".")
        if decision.get("add"):
            new, bad = cutting.parse_cut_lines(decision["add"], self.cues, self.dur, source="user")
            for c in new:
                c.source = "user"
            self.cuts += new
            if new:
                msgs.append(f"Tilføjet {len(new)} klip: " + "; ".join(
                    f"{fmt_clock(c.start)}-{fmt_clock(c.end)}" for c in new) + ".")
            if bad:
                msgs.append("Kunne ikke læse: " + " | ".join(bad))
        self._decision_msg = " ".join(msgs) or "Ingen ændringer."
        log.info("cut decision from Claude: %s", self._decision_msg)
        self._changed()

    # --- Done -------------------------------------------------------------------------------

    def _approve(self):
        on = [c for c in self.cuts if c.on]
        if not on:
            return self._skip()
        self._finish(on)

    def _skip(self):
        self._finish([])

    def _finish(self, cuts: list[Cut]):
        done, self.on_done = self.on_done, None
        if done:
            log.info("cuts approved: %s", [(c.start, c.end, c.kind, c.source) for c in cuts])
            done(cuts, [c for c in self.cuts])

    def _on_destroy(self, e):
        if e.widget is self:
            self._hide_scrub()
            self._scrub_one.stop()
            self.scrub.close()
            self.runner.cancel()
            shutil.rmtree(self.tmp, ignore_errors=True)
