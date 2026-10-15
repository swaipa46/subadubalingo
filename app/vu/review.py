"""The cut review: a timeline (loudness, speech, cuts) where the user drags the edges of the cuts, draws new ones and
switches them off, a list of the cuts, and a preview of each (frames before/after, play the transition).

Shown in the left column of the main window when the run reaches "Du gennemser klippene". When Claude drives the
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

from . import cutting, jobs
from .common import Cue, Runner, log
from .cutting import KINDS, Cut, fmt_clock, fmt_length
from .gui import ACCENT, ACCENT_DARK, ACCENT_LIGHT, CARD_BG, GREY, GREEN

EDGE_PX = 7
MIN_VIEW_MS = 8_000


class CutReview(ttk.Frame):
    def __init__(self, master, app, video: Path, duration: int, cues: list[Cue], cuts: list[Cut],
                 env: np.ndarray | None, on_done, mcp_job: str | None = None):
        super().__init__(master)
        self.app, self.video, self.dur, self.cues = app, video, max(1, duration), cues
        self.cuts = sorted(cuts, key=lambda c: c.start)
        self.env = env
        self.on_done, self.mcp_job = on_done, mcp_job
        self.k = k = app.scale
        self.v0, self.v1 = 0, self.dur
        self.sel: Cut | None = None
        self.drag: dict | None = None
        self.hover_t: int | None = None
        self.runner = Runner()
        self.tmp = Path(tempfile.mkdtemp(prefix="vu-review-"))
        self.q: queue.Queue = queue.Queue()
        self._img_gen = 0
        self._images: list = []
        self._dirty_snapshot = True
        self._decision_msg = ""
        self.starts = [c.start for c in cues]
        self.snaps = sorted({0, self.dur} | {c.end for c in cues} |
                            {max(cues[i - 1].end if i else 0, c.start - 200) for i, c in enumerate(cues)})
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)   # extra height goes to the timeline; the list is as tall as it needs
        self.rowconfigure(6, weight=0)
        self._build()
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.after(50, self._redraw)
        self.after(120, self._poll)

    # --- Layout ---------------------------------------------------------------------------

    def _build(self):
        k = self.k
        head = ttk.Frame(self)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text="Gennemse klippene", style="Section.TLabel", foreground=ACCENT_DARK).grid(row=0, column=0, sticky="w")
        zoom = ttk.Frame(head)
        zoom.grid(row=0, column=2, sticky="e")
        ttk.Button(zoom, text="−", width=3, command=lambda: self._zoom(1.6)).pack(side="left")
        ttk.Button(zoom, text="+", width=3, command=lambda: self._zoom(1 / 1.6)).pack(side="left", padx=4)
        ttk.Button(zoom, text="Hele videoen", command=self._zoom_all).pack(side="left")
        self.summary = ttk.Label(self, text="", font="SunValleyBodyStrongFont", foreground=ACCENT)
        self.summary.grid(row=1, column=0, sticky="w", pady=(4, 0))
        n_claude = sum(1 for c in self.cuts if c.source == "claude")
        n_auto = sum(1 for c in self.cuts if c.source == "auto")
        found = (f"Claude har foreslået {n_claude} klip, og programmet har fundet {n_auto} lange pauser. "
                 if self.cuts else "Hverken Claude eller programmet har fundet noget at klippe. ")
        hint = ttk.Label(self, style="Hint.TLabel", justify="left", text=(
            found + "De farvede felter klippes væk. Klik på et klip for at se det, og træk i kanterne for at rette det."
            + (" Du kan også sige det til Claude i chatten, fx \"behold det ved 14:20\"." if self.mcp_job else "")))
        hint.grid(row=2, column=0, sticky="ew", pady=(2, 8))
        self.app._wrap(hint, self, 10)

        self.canvas = tk.Canvas(self, height=int(150 * k), background="white", highlightthickness=1,
                                highlightbackground=ACCENT_LIGHT, cursor="crosshair")
        self.canvas.grid(row=3, column=0, sticky="nsew")
        self.xbar = ttk.Scrollbar(self, orient="horizontal", command=self._xview)
        self.xbar.grid(row=4, column=0, sticky="ew")
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
        self.hover = ttk.Label(self, text=" ", style="Hint.TLabel")
        self.hover.grid(row=5, column=0, sticky="ew", pady=(4, 6))

        mid = self.mid = ttk.Frame(self)
        self._mid_min = 0
        mid.grid(row=6, column=0, sticky="nsew")
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(0, weight=1)
        cols = ("nr", "tid", "hvorfor")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse", height=self._tree_rows())
        for col, title, width, stretch in (("nr", "#", 48, False), ("tid", "Tid", 150, False),
                                           ("hvorfor", "Hvorfor", 220, True)):
            self.tree.heading(col, text=title, anchor="w")
            self.tree.column(col, width=int(width * k), stretch=stretch, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        keys = ttk.Label(mid, style="Hint.TLabel", justify="left", text=(
            "Mellemrum eller dobbeltklik: behold / klip væk igen  ·  Delete: slet klip  ·  "
            "Musehjul: zoom  ·  Shift + musehjul: rul til siden"))
        keys.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.tag_configure("off", foreground=GREY)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-Button-1>", self._on_tree_double)
        self.tree.bind("<space>", lambda e: self._toggle(self.sel))
        self.tree.bind("<Delete>", lambda e: self._delete(self.sel))

        det = ttk.Frame(mid, padding=(14, 0, 0, 0))
        det.grid(row=0, column=2, rowspan=2, sticky="nsew")
        self.d_title = ttk.Label(det, text="", font="SunValleyBodyStrongFont")
        self.d_title.grid(row=0, column=0, columnspan=2, sticky="w")
        self.d_text = ttk.Label(det, text="Klik på et klip for at se det.", style="Hint.TLabel", justify="left",
                                wraplength=int(340 * k))
        self.d_text.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 6))
        self.img_a = tk.Label(det, background=CARD_BG, borderwidth=0)
        self.img_a.grid(row=2, column=0, sticky="nw")
        self.img_b = tk.Label(det, background=CARD_BG, borderwidth=0)
        self.img_b.grid(row=2, column=1, sticky="nw", padx=(6, 0))
        self.cap_a = ttk.Label(det, text="", style="Hint.TLabel")
        self.cap_a.grid(row=3, column=0, sticky="w")
        self.cap_b = ttk.Label(det, text="", style="Hint.TLabel")
        self.cap_b.grid(row=3, column=1, sticky="w", padx=(6, 0))
        acts = ttk.Frame(det)
        acts.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.b_trans = ttk.Button(acts, text="▶  Se overgangen", command=lambda: self._play("trans"))
        self.b_trans.grid(row=0, column=0, sticky="w")
        self.b_cut = ttk.Button(acts, text="▶  Se det, der klippes væk", command=lambda: self._play("cut"))
        self.b_cut.grid(row=0, column=1, sticky="w", padx=(6, 0))
        self.b_toggle = ttk.Button(acts, text="Behold i videoen", command=lambda: self._toggle(self.sel))
        self.b_toggle.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.b_delete = ttk.Button(acts, text="Slet klip", command=lambda: self._delete(self.sel))
        self.b_delete.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(6, 0))
        self.detail_btns = (self.b_trans, self.b_cut, self.b_toggle, self.b_delete)

        bottom = ttk.Frame(self)
        bottom.grid(row=7, column=0, sticky="ew", pady=(14, 0))
        self.b_ok = ttk.Button(bottom, text="Klip videoen", style="Accent.TButton", command=self._approve)
        self.b_ok.pack(side="left", ipady=4)
        ttk.Button(bottom, text="Spring klip over", command=self._skip).pack(side="left", padx=10)
        ttk.Button(bottom, text="Annullér", command=self.app._cancel).pack(side="left")
        self.b_hint = ttk.Label(bottom, text="", style="Hint.TLabel")
        self.b_hint.pack(side="left", padx=12)
        self._refresh_list()
        self._show_details()
        if self.cuts:   # start on the first cut, so the details and its buttons are there from the start
            self.after_idle(lambda: self.cuts and self.sel is None and self._select(self.cuts[0], scroll=False))

    def _tree_rows(self) -> int:
        return min(max(len(self.cuts), 3), 8)

    # --- Geometry -------------------------------------------------------------------------

    def _width(self) -> int:
        return max(100, self.canvas.winfo_width() - 2 * self._m())

    def _m(self) -> int:
        return int(8 * self.k)

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
        k, m = self.k, self._m()
        w, h = c.winfo_width(), c.winfo_height()
        top, bottom = int(22 * k), h - int(6 * k)
        mid = (top + bottom) / 2 - 4 * k
        amp = (bottom - top) / 2 - 10 * k
        # cut fills first, under the sound
        for cut in self.cuts:
            if cut.on and cut.end > self.v0 and cut.start < self.v1:
                x0, x1 = max(m, self.x_of(cut.start)), min(w - m, self.x_of(cut.end))
                c.create_rectangle(x0, top, x1, bottom, fill=KINDS[cut.kind][2], outline="")
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
                c.create_polygon(upper + lower, fill="#b9d4ec", outline="")
        # speech
        y0, y1 = bottom - 10 * k, bottom - 3 * k
        i = max(0, bisect.bisect_right(self.starts, self.v0) - 1)
        last_x = -10
        while i < len(self.cues) and self.cues[i].start < self.v1:
            q = self.cues[i]
            x0, x1 = self.x_of(q.start), self.x_of(q.end)
            if x1 >= m and x1 - last_x >= 1:
                c.create_rectangle(max(m, x0), y0, min(w - m, max(x1, x0 + 1)), y1, fill=ACCENT, outline="")
                last_x = x1
            i += 1
        # cut outlines, handles and labels
        for n, cut in enumerate(self.cuts, 1):
            if cut.end <= self.v0 or cut.start >= self.v1:
                continue
            strong = KINDS[cut.kind][1]
            x0, x1 = self.x_of(cut.start), self.x_of(cut.end)
            selected = cut is self.sel
            if cut.on:
                c.create_rectangle(x0, top, x1, bottom, outline=strong, width=3 * k if selected else 1.5 * k)
            else:
                c.create_rectangle(x0, top, x1, bottom, outline=GREY, dash=(4, 3), width=2 * k if selected else 1)
            if selected:
                for x in (x0, x1):
                    c.create_rectangle(x - 3 * k, mid - 14 * k, x + 3 * k, mid + 14 * k, fill=strong if cut.on else GREY,
                                       outline="white")
            label = f"{n}  {KINDS[cut.kind][0]}" if cut.on else f"{n}  beholdes"
            if x1 - x0 > 150 * k:
                c.create_text(max(x0, m) + 6 * k, top + 4 * k, text=label, anchor="nw", fill=strong if cut.on else GREY,
                              font="SunValleyCaptionFont")
            elif x1 - x0 > 14 * k:
                c.create_text((x0 + x1) / 2, top + 4 * k, text=str(n), anchor="n", fill=strong if cut.on else GREY,
                              font="SunValleyCaptionFont")
        # ruler
        c.create_rectangle(0, 0, w, top, fill=CARD_BG, outline="")
        c.create_line(0, top, w, top, fill=ACCENT_LIGHT)
        span_s = (self.v1 - self.v0) / 1000
        px_per_s = self._width() / max(0.001, span_s)
        step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600) if s * px_per_s >= 80 * k), 3600)
        t = (self.v0 // 1000 // step) * step
        while t * 1000 <= self.v1:
            x = self.x_of(t * 1000)
            if x >= m - 1:
                c.create_line(x, top - 6 * k, x, top, fill=GREY)
                c.create_text(x + 3 * k, top - 4 * k, text=fmt_clock(t * 1000), anchor="sw", fill=GREY,
                              font="SunValleyCaptionFont")
            t += step
        # hover line
        if self.hover_t is not None and self.v0 <= self.hover_t <= self.v1:
            x = self.x_of(self.hover_t)
            c.create_line(x, top, x, bottom, fill=ACCENT_DARK)
        self.xbar.set(self.v0 / self.dur, self.v1 / self.dur)

    # --- Mouse ----------------------------------------------------------------------------

    def _hit(self, x: float):
        """('edge', cut, 'start'|'end') or ('cut', cut) or None. The selected cut wins."""
        order = ([self.sel] if self.sel else []) + [c for c in self.cuts if c is not self.sel]
        for cut in order:
            for side, t in (("start", cut.start), ("end", cut.end)):
                if abs(self.x_of(t) - x) <= EDGE_PX * self.k:
                    return ("edge", cut, side)
        for cut in order:
            if self.x_of(cut.start) <= x <= self.x_of(cut.end):
                return ("cut", cut)
        return None

    def _set_hover(self, t: int | None):
        self.hover_t = t
        if t is None:
            self.hover.configure(text=" ")
        else:
            text = cutting.text_at(self.cues, t)
            inside = next((c for c in self.cuts if c.start <= t <= c.end), None)
            where = f"   [{KINDS[inside.kind][0].lower() if inside.on else 'beholdes'}]" if inside else ""
            self.hover.configure(text=f"{fmt_clock(t)}{where}   " + (f"“{text}”" if text else "(ingen tale)"))
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
        elif d["mode"] == "new" and d["moved"]:
            a, b = sorted((self._snap(d["t0"]), t))
            if d["cut"] is None:
                d["cut"] = Cut(a, max(b, a + 1), "manuel", "Dit eget klip", True, "user")
                self.cuts.append(d["cut"])
                self.sel = d["cut"]
            d["cut"].start, d["cut"].end = a, max(b, a + 1)
        self._set_hover(self.t_of(e.x))

    def _on_release(self, e):
        d, self.drag = self.drag, None
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
            why = KINDS[cut.kind][0] + (f": {cut.reason}" if cut.reason else "")
            self.tree.insert("", "end", iid=str(n), tags=() if cut.on else ("off",), values=(
                f"{n}." if cut.on else f"({n})", f"{fmt_clock(cut.start)} - {fmt_clock(cut.end)}  ({fmt_length(cut.length)})",
                why if cut.on else "Beholdes - " + why))
        if self.sel in self.cuts:
            iid = str(self.cuts.index(self.sel) + 1)
            self.tree.selection_set(iid)
            self.tree.see(iid)
        ranges = cutting.active(self.cuts, self.dur)
        removed = cutting.removed_ms(ranges)
        n_on = sum(1 for c in self.cuts if c.on)
        if n_on:
            self.summary.configure(text=f"{n_on} klip fjerner {fmt_length(removed)}. Videoen bliver "
                                        f"{fmt_clock(self.dur - removed)} lang (nu {fmt_clock(self.dur)}).")
            self.b_ok.configure(text=f"Klip videoen ({n_on} klip)", state="normal")
            self.b_hint.configure(text="")
        else:
            self.summary.configure(text=f"Ingen klip valgt. Videoen er {fmt_clock(self.dur)} lang.")
            self.b_ok.configure(text="Klip videoen", state="disabled")
            self.b_hint.configure(text="Intet at klippe? Tryk \"Spring klip over\".")
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
            self.d_text.configure(text="Klik på et klip for at se det. Klik på et tomt sted for at se billedet der.")
            return
        n = self.cuts.index(cut) + 1 if cut in self.cuts else 0
        self.d_title.configure(text=f"Klip {n}: {KINDS[cut.kind][0]}", foreground=KINDS[cut.kind][1] if cut.on else GREY)
        said = cutting.excerpt(self.cues, cut.start, cut.end, 110)   # short: the timeline needs the height
        text = ((cut.reason + "\n" if cut.reason else "")
                + f"{fmt_clock(cut.start)} - {fmt_clock(cut.end)} ({fmt_length(cut.length)})"
                + (f"\n“{said}”" if said else "\n(ingen tale)")
                + ("" if cut.on else "\nBeholdes i videoen."))
        self.d_text.configure(text=text + "\n" * max(0, 3 - text.count("\n")))   # about the same height for every cut
        self.after_idle(self._hold_height)
        self.b_toggle.configure(text="Behold i videoen" if cut.on else "Klip væk alligevel")
        self.cap_a.configure(text=f"Lige før  {fmt_clock(cut.start)}")
        self.cap_b.configure(text=f"Lige efter  {fmt_clock(cut.end)}")
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
            self.rowconfigure(6, minsize=h)

    def _show_frame_at(self, t: int):
        self.cap_a.configure(text=f"Billedet ved {fmt_clock(t)}")
        self.cap_b.configure(text="")
        self.img_b.configure(image="")
        self._load_frames([(t, self.img_a)])

    def _load_frames(self, items):
        self._img_gen += 1
        gen = self._img_gen
        width = int(165 * self.k)

        def work():
            for ms, label in items:
                out = self.tmp / f"f{gen}-{ms}.png"
                try:
                    ok = cutting.frame_png(self.runner, self.video, ms, out, width)
                except Exception:
                    ok = False
                self.q.put(("img", gen, label, out if ok else None))
        threading.Thread(target=work, daemon=True).start()

    def _play(self, what: str):
        cut = self.sel
        if not cut:
            return
        btn = self.b_trans if what == "trans" else self.b_cut
        old = btn.cget("text")
        btn.configure(text="Laver klippet ...", state="disabled")
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
            self.runner.cancel()
            shutil.rmtree(self.tmp, ignore_errors=True)
