"""Indstillinger -> "Undertekster i videoen": how burned-in subtitles look (burnin.Look), with a live sample
(design T8, assets/ui/README.md "Burned-in subtitles"; mockup design/mockups/*/burnin-settings.png).

Left the controls (size S M L, Nederst/Øverst, a dark box, a logo in a corner), right the sample: a still from the
last video (or a gradient when there is none) with a sample line burned in by the same ffmpeg chain as a real run, so
what the user sees is what they get. ffmpeg runs in a thread; a change while it runs is drawn when it is done (only
the newest look)."""
from __future__ import annotations

import shutil
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from . import burnin, skin
from .common import Runner, log
from .i18n import t
from .pickers import Tooltip, _Picker
from .theme import IMG, P


class Segmented(_Picker):
    """A few choices side by side in a secondary-button frame (like the header's pickers), bound to a StringVar.
    text=True uses Segoe UI for words; the default is the pixel font (for S M L)."""

    def __init__(self, parent, variable: tk.StringVar, choices: list[tuple[str, str, str]], text: bool = False):
        super().__init__(parent)
        self.var = variable
        for value, label, tip in choices:
            self.add(value, tip, text=label)
            if text:
                self.items[value].configure(style="Text.PickItem.TLabel")
        self.refresh(variable.get())
        variable.trace_add("write", lambda *a: self.refresh(variable.get()))

    def pick(self, value: str):
        self.var.set(value)


class CornerPicker(ttk.Frame):
    """The logo's corner: a 2x2 grid of 18x12 cells in an input frame. The chosen cell is filled (heading on Lys,
    accent on Nat), the others are 2 px outlines. Click a cell, or use the arrow keys."""

    def __init__(self, parent, variable: tk.StringVar):
        k = 2 if IMG.two else 1
        # padding on the widget: skin_cards renames the style to Card.Input.Burnin.TFrame
        super().__init__(parent, style="Input.Burnin.TFrame", takefocus=True, cursor="hand2", padding=(9 * k, 8 * k))
        self.var, self.k, self.enabled = variable, k, True
        self.cw, self.ch, self.gap = 18 * k, 12 * k, 6 * k
        self.canvas = tk.Canvas(self, width=2 * self.cw + self.gap, height=2 * self.ch + self.gap,
                                background=P.input_bg, highlightthickness=0, borderwidth=0, cursor="hand2")
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._click)
        self.bind("<Button-1>", lambda e: self.focus_set())
        for key, (dx, dy) in (("<Left>", (-1, 0)), ("<Right>", (1, 0)), ("<Up>", (0, -1)), ("<Down>", (0, 1))):
            self.bind(key, lambda e, dx=dx, dy=dy: self._move(dx, dy))
        self.tip = Tooltip(self.canvas, lambda: t(f"burnin.corner_{self.var.get()}"))
        variable.trace_add("write", lambda *a: self.draw())
        self.draw()

    def _cell(self, corner: str) -> tuple[int, int]:
        return (0 if corner[1] == "l" else self.cw + self.gap), (0 if corner[0] == "t" else self.ch + self.gap)

    def draw(self):
        c, k = self.canvas, self.k
        c.delete("all")
        colour = P.heading if self.enabled else P.disabled_text
        fill = (P.heading if P.name == "light" else P.accent) if self.enabled else P.disabled_text
        for corner in burnin.CORNERS:
            x, y = self._cell(corner)
            if corner == self.var.get():
                c.create_rectangle(x, y, x + self.cw, y + self.ch, fill=fill, width=0)
            else:   # a 2 px outline inside the cell
                c.create_rectangle(x + k, y + k, x + self.cw - k, y + self.ch - k, outline=colour, width=2 * k)

    def _click(self, e):
        if not self.enabled:
            return
        self.focus_set()
        col = "l" if e.x < self.cw + self.gap // 2 else "r"
        row = "t" if e.y < self.ch + self.gap // 2 else "b"
        self.var.set(row + col)

    def _move(self, dx: int, dy: int):
        if not self.enabled:
            return
        row, col = self.var.get()
        row = "t" if dy < 0 else "b" if dy > 0 else row
        col = "l" if dx < 0 else "r" if dx > 0 else col
        self.var.set(row + col)

    def set_enabled(self, on: bool):
        self.enabled = on
        self.configure(cursor="hand2" if on else "arrow", takefocus=on)
        self.canvas.configure(cursor="hand2" if on else "arrow")
        self.draw()


def _styles():
    """The styles this card adds: the picker item in Segoe UI, and an input frame around the corner picker."""
    style = ttk.Style()
    style.configure("Text.PickItem.TLabel", font="SunValleyBodyFont")
    k = 2 if IMG.two else 1
    try:
        field = style.layout("TCombobox")[0][0]   # the input frame element skin.apply made (frames/input)
        for name in ("Input.Burnin.TFrame", "Card.Input.Burnin.TFrame"):
            style.layout(name, [(field, {"sticky": "nsew", "children": [("Frame.padding", {"sticky": "nsew"})]})])
            style.configure(name, background=P.card)
    except (tk.TclError, IndexError):
        pass


class BurninCard(skin.Card):
    """The settings card. save_to(settings) writes the choices; the sample's temp folder goes with the card."""

    def __init__(self, parent, settings):
        super().__init__(parent, text=t("burnin.title"), padding=12)
        _styles()
        k = 2 if IMG.two else 1
        last = getattr(settings, "last_video", "") or ""
        self.video = Path(last) if last else None
        look = burnin.Look.from_settings(settings)
        self.size = tk.StringVar(value=look.size)
        self.position = tk.StringVar(value=look.position)
        self.box = tk.BooleanVar(value=look.box)
        self.logo_on = tk.BooleanVar(value=bool(getattr(settings, "burnin_logo_on", False)))
        self.logo = tk.StringVar(value=getattr(settings, "burnin_logo", "") or "")
        self.corner = tk.StringVar(value=look.logo_corner)

        ttk.Label(self, text=t("burnin.hint"), style="Hint.TLabel").grid(row=0, column=0, columnspan=2, sticky="w",
                                                                         pady=(0, 12 * k))
        left = ttk.Frame(self)
        left.grid(row=1, column=0, sticky="nw", padx=(0, 24 * k))
        left.columnconfigure(1, weight=1)
        ttk.Label(left, text=t("burnin.size")).grid(row=0, column=0, sticky="w", padx=(0, 16 * k), pady=(0, 10 * k))
        Segmented(left, self.size, [(v, v.upper(), t(f"burnin.size_{v}")) for v in burnin.SIZES]).grid(
            row=0, column=1, sticky="w", pady=(0, 10 * k))
        ttk.Label(left, text=t("burnin.position")).grid(row=1, column=0, sticky="w", padx=(0, 16 * k),
                                                        pady=(0, 12 * k))
        Segmented(left, self.position, [(v, t(f"burnin.{v}"), t(f"burnin.{v}_tip")) for v in burnin.POSITIONS],
                  text=True).grid(row=1, column=1, sticky="w", pady=(0, 12 * k))
        ttk.Checkbutton(left, text=t("burnin.box"), variable=self.box).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(left, text=t("burnin.box_hint"), style="Hint.TLabel").grid(
            row=3, column=0, columnspan=2, sticky="w", padx=(32 * k, 0), pady=(0, 12 * k))
        ttk.Checkbutton(left, text=t("burnin.logo"), variable=self.logo_on, command=self._logo_toggled).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(0, 8 * k))
        logo_row = ttk.Frame(left)
        logo_row.grid(row=5, column=0, columnspan=2, sticky="w", padx=(32 * k, 0))
        self.logo_btn = skin.IconButton(logo_row, "show-in-folder", text="", command=self._pick_logo)
        self.logo_btn.pack(side="left", anchor="center")
        Tooltip(self.logo_btn, lambda: self.logo.get() or t("burnin.pick_logo"))
        self.corners = CornerPicker(logo_row, self.corner)
        self.corners.pack(side="left", padx=(16 * k, 0), anchor="center")

        # The sample: a fixed 16:9 box with a 2 px border, so the dialog does not jump while the picture is made
        self.sample_w, self.sample_h = 420 * k, 236 * k
        right = ttk.Frame(self)
        right.grid(row=1, column=1, sticky="ne")
        self.frame = tk.Frame(right, background=P.card_border, width=self.sample_w + 4 * k,
                              height=self.sample_h + 4 * k)
        self.frame.pack_propagate(False)
        self.frame.pack(anchor="w")
        self.picture = tk.Label(self.frame, background=P.timeline_bg, borderwidth=0, highlightthickness=0,
                                foreground=P.hint, compound="center")
        self.picture.pack(fill="both", expand=True, padx=2 * k, pady=2 * k)
        self.caption = ttk.Label(right, text=t("burnin.caption"), style="Hint.TLabel")
        self.caption.pack(anchor="w", pady=(6 * k, 0))
        self.columnconfigure(1, weight=1)

        self.runner = Runner()
        self.tmp = Path(tempfile.mkdtemp(prefix="subadubalingo-burnin-"))
        self.base: Path | None = None     # the still behind the sample, made once
        self.image: tk.PhotoImage | None = None
        self._busy = False
        self._again = False
        self._result: tuple[bool, Path | None] | None = None
        for var in (self.size, self.position, self.box, self.logo_on, self.logo, self.corner):
            var.trace_add("write", lambda *a: self._changed())
        self.bind("<Destroy>", lambda e: self.close() if e.widget is self else None, add="+")
        self._changed()

    # --- choices ------------------------------------------------------------------------------

    def look(self) -> burnin.Look:
        return burnin.Look(self.size.get(), self.position.get(), self.box.get(),
                           self.logo.get() if self.logo_on.get() else "", self.corner.get())

    def save_to(self, s):
        look = self.look()
        s.burnin_size, s.burnin_position, s.burnin_box = look.size, look.position, look.box
        s.burnin_logo_on, s.burnin_logo, s.burnin_logo_corner = self.logo_on.get(), self.logo.get(), look.logo_corner

    def _logo_toggled(self):
        if self.logo_on.get() and not self.logo.get():
            self._pick_logo()
            if not self.logo.get():
                self.logo_on.set(False)

    def _pick_logo(self):
        start = Path(self.logo.get()).parent if self.logo.get() else Path.home() / "Pictures"
        p = filedialog.askopenfilename(parent=self.winfo_toplevel(), title=t("burnin.pick_logo_title"),
                                       initialdir=str(start),
                                       filetypes=[(t("burnin.filetype_image"), " ".join("*" + x for x in burnin.LOGO_TYPES)),
                                                  (t("gui.filetype.all"), "*.*")])
        if p:
            self.logo.set(str(Path(p)))
            self.logo_on.set(True)

    def _changed(self):
        name = Path(self.logo.get()).name if self.logo.get() else t("burnin.pick_logo")
        if len(name) > 28:
            name = name[:16] + "…" + name[-10:]
        self.logo_btn.configure(text=name)
        on = self.logo_on.get()
        self.logo_btn.state(["!disabled"] if on or not self.logo.get() else ["disabled"])
        self.corners.set_enabled(on)
        self._render()

    # --- the live sample ------------------------------------------------------------------------

    def _render(self):
        if self._busy:
            self._again = True
            return
        self._busy, self._again = True, False
        if self.image is None:
            self.picture.configure(text=t("burnin.sample_busy"))
        look, text = self.look(), t("burnin.sample")
        threading.Thread(target=self._work, args=(look, text), daemon=True).start()
        self.after(60, self._poll)

    def _work(self, look: burnin.Look, text: str):
        try:
            if self.base is None:
                base = self.tmp / "frame.png"
                if not burnin.sample_frame(self.runner, self.video, base, self.sample_w):
                    self._result = (False, None)
                    return
                self.base = base
            out = self.tmp / "sample.png"
            self._result = (burnin.render_sample(self.runner, look, self.base, text, out), out)
        except Exception:
            log.exception("burn-in sample failed")
            self._result = (False, None)

    def _poll(self):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        if self._result is None:
            self.after(60, self._poll)
            return
        ok, out = self._result
        self._result, self._busy = None, False
        if ok and out:
            try:
                self.image = tk.PhotoImage(master=self, file=str(out))
                self.picture.configure(image=self.image, text="")
            except tk.TclError:
                ok = False
        if not ok:
            self.picture.configure(image="", text=t("burnin.sample_failed"))
            self.image = None
        if self._again:
            self._render()

    def close(self):
        self.runner.cancel()
        shutil.rmtree(self.tmp, ignore_errors=True)
