"""The small choosers of the app's look and language (design: assets/ui/README.md "Header"):
- in the header, the theme picker (Lys, Nat, Følg Windows) and the language picker (DA SV NO EN): items in a
  secondary-button frame, the current one highlighted, a tooltip in the item's own language;
- in Indstillinger and on the first start, the same choices as tiles (Tiles).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import i18n, theme
from .i18n import NATIVE, t
from .theme import IMG, P


class Tooltip:
    """A small label under a widget while the mouse is over it, in the theme's tooltip colours."""

    def __init__(self, widget, text):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, e=None):
        self._hide()
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        text = self.text() if callable(self.text) else self.text
        tk.Label(self.tip, text=text, background=P.tooltip_bg, foreground=P.tooltip_text, padx=8, pady=4,
                 font="SunValleyCaptionFont").pack()
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip.wm_geometry(f"+{x}+{y}")

    def _hide(self, e=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class _Picker(ttk.Frame):
    """Items side by side in a secondary-button frame; click or Enter/Space picks, the arrow keys move."""

    def __init__(self, parent, lead_icon: str | None = None):
        k = 2 if IMG.two else 1
        super().__init__(parent, style="Picker.TFrame", padding=(6 * k, 4 * k, 9 * k, 8 * k))
        self.items: dict[str, ttk.Label] = {}
        if lead_icon:   # a leading icon that only says what the picker is (the globe)
            ttk.Label(self, image=IMG.get(f"icons/{lead_icon}"), style="PickItem.TLabel").pack(side="left")

    def add(self, value: str, tip, **label):
        # foreground="": the style decides (sv_ttk puts a colour on every label through the option database)
        item = ttk.Label(self, style="PickItem.TLabel", takefocus=True, cursor="hand2", foreground="", **label)
        item.pack(side="left")
        item.bind("<Button-1>", lambda e: self.pick(value))
        item.bind("<Enter>", lambda e: item.state(["active"]))
        item.bind("<Leave>", lambda e: item.state(["!active"]))
        for key in ("<Return>", "<space>"):
            item.bind(key, lambda e: self.pick(value))
        item.bind("<Left>", lambda e: self._move(value, -1))
        item.bind("<Right>", lambda e: self._move(value, 1))
        Tooltip(item, tip)
        self.items[value] = item

    def _move(self, value: str, step: int):
        keys = list(self.items)
        self.items[keys[(keys.index(value) + step) % len(keys)]].focus_set()

    def pick(self, value: str):
        raise NotImplementedError

    def refresh(self, current: str):
        for value, item in self.items.items():
            item.state(["selected"] if value == current else ["!selected"])


class ThemePicker(_Picker):
    """Lys, Nat and Følg Windows as three pixel icons (Indstillinger -> Udseende has the same)."""
    CHOICES = (("light", "theme-light"), ("night", "theme-night"), ("system", "theme-system"))

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.icons = dict(self.CHOICES)
        for choice, icon in self.CHOICES:
            self.add(choice, lambda c=choice: self._tip(c), image=IMG.get(f"icons/{icon}"))
        self.refresh()

    @staticmethod
    def _tip(choice: str) -> str:
        label = t(f"gui.theme.{choice}")
        if choice == "system":
            now = t("gui.theme.light") if theme.resolve("system") == "light" else t("gui.theme.night")
            return t("gui.theme.tip_system", label=label, now=now)
        return t("gui.theme.tip", label=label)

    def pick(self, value: str):
        self.app.set_theme(value)

    def refresh(self, current: str | None = None):
        current = current or self.app.settings.theme
        super().refresh(current)
        for choice, item in self.items.items():
            item.configure(image=IMG.get(f"icons/{self.icons[choice]}{'-selected' if choice == current else ''}"))


class LanguagePicker(_Picker):
    """The app's language: a globe, then DA SV NO EN in the pixel font. The tooltip is in the language it names
    ("Svenska · byt programmets språk"), so someone who does not read the current one still finds theirs."""

    def __init__(self, parent, app):
        super().__init__(parent, lead_icon="language")
        self.app = app
        for code in i18n.CODES:
            self.add(code, lambda c=code: t("pickers.lang_tip", lang=c, name=NATIVE[c]), text=code.upper())
        self.refresh(i18n.current())

    def pick(self, value: str):
        self.app.set_language(value)


class Tiles(ttk.Frame):
    """Choices as tiles in a row (or a grid of columns): each a pixel code or icon, a name and an optional hint, with
    a check box in the corner. variable holds the chosen value; on_change runs after a pick."""

    def __init__(self, parent, variable: tk.StringVar, choices, columns: int | None = None, on_change=None,
                 background: str | None = None):
        super().__init__(parent)
        self.var, self.on_change = variable, on_change
        self.tiles: dict[str, tuple[ttk.Frame, ttk.Label]] = {}
        k = 2 if IMG.two else 1
        columns = columns or len(choices)
        for i, (value, code, icon, name, hint) in enumerate(choices):
            tile = ttk.Frame(self, style="Tile.TFrame", takefocus=True, cursor="hand2",
                             padding=(14 * k, 10 * k, 18 * k, 14 * k))
            tile.grid(row=i // columns, column=i % columns, sticky="nsew", padx=(0, 10 * k), pady=(0, 10 * k))
            self.columnconfigure(i % columns, weight=1, uniform="tiles")
            top = ttk.Frame(tile, style="Tile.Inner.TFrame")
            top.pack(fill="x")
            if code:
                ttk.Label(top, text=code, style="TileCode.TLabel").pack(side="left")
            if icon:
                ttk.Label(top, image=IMG.get(f"icons/{icon}"), style="TileCode.TLabel").pack(side="left")
            box = ttk.Label(top, image=IMG.get("icons/step-pending"), style="TileCode.TLabel")
            box.pack(side="right", anchor="n")
            ttk.Label(tile, text=name, style="TileName.TLabel").pack(anchor="w", pady=(6 * k, 0))
            if hint:   # wraps to the tile's width (Swedish and Norwegian hints need two lines in Indstillinger)
                h = ttk.Label(tile, text=hint, style="TileHint.TLabel", justify="left")
                h.pack(anchor="w", fill="x", pady=(4 * k, 0))
                tile.bind("<Configure>", lambda e, h=h: h.configure(wraplength=max(80, e.width - 34 * k)), add="+")
            self._bind(tile, value)
            self.tiles[value] = (tile, box)
        self.refresh()
        variable.trace_add("write", lambda *a: self.refresh())

    def _bind(self, tile, value):
        def walk(w):
            w.bind("<Button-1>", lambda e: self.pick(value))
            w.bind("<Enter>", lambda e: tile.state(["active"]))
            w.bind("<Leave>", lambda e: tile.state(["!active"]))
            for child in w.winfo_children():
                walk(child)
        walk(tile)
        for key in ("<Return>", "<space>"):
            tile.bind(key, lambda e: self.pick(value))
        tile.bind("<Left>", lambda e: self._move(value, -1))
        tile.bind("<Right>", lambda e: self._move(value, 1))
        tile.bind("<Up>", lambda e: self._move(value, -1))
        tile.bind("<Down>", lambda e: self._move(value, 1))

    def _move(self, value: str, step: int):
        keys = list(self.tiles)
        self.tiles[keys[(keys.index(value) + step) % len(keys)]][0].focus_set()

    def pick(self, value: str):
        self.var.set(value)
        if self.on_change:
            self.on_change(value)

    def refresh(self):
        for value, (tile, box) in self.tiles.items():
            on = value == self.var.get()
            tile.state(["selected"] if on else ["!selected"])
            box.configure(image=IMG.get(f"icons/step-{'done' if on else 'pending'}"))


def language_tiles(parent, variable: tk.StringVar, columns: int | None = None, on_change=None) -> Tiles:
    """DA Dansk / SV Svenska / NO Norsk / EN English, each with a hint in its own language."""
    return Tiles(parent, variable, [(c, c.upper(), None, NATIVE[c], t("pickers.lang_tile_hint", lang=c))
                                    for c in i18n.CODES], columns=columns, on_change=on_change)


def theme_tiles(parent, variable: tk.StringVar, on_change=None) -> Tiles:
    return Tiles(parent, variable, [(c, None, f"theme-{c}", t(f"gui.theme.{c}"), None)
                                    for c in ("light", "night", "system")], on_change=on_change)
