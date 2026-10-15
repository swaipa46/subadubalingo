"""The ttk side of a theme: the Sun Valley theme (light or dark) as the base, then the design session's colours, pixel
frames and icons on top (assets/ui, see theme.py). Cards: every ttk LabelFrame is drawn with the card frame, and
skin_cards() gives the widgets inside a card the card's background (ttk widgets cannot be transparent).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from .common import log
from .theme import IMG, P, PIXEL_FONT

BODY_SIZES = {"SunValleyCaptionFont": 9, "SunValleyBodyFont": 11, "SunValleyBodyStrongFont": 11,
              "SunValleyBodyLargeFont": 13, "SunValleySubtitleFont": 15, "SunValleyTitleFont": 20,
              "TkDefaultFont": 11, "TkTextFont": 11, "TkHeadingFont": 11, "TkMenuFont": 11}
CARD_CLASSES = ("TFrame", "TLabel", "TCheckbutton", "TRadiobutton", "TButton")


def _element(style: ttk.Style, name: str, frame: str, states: dict[str, str] | None = None, padding=None):
    """A ttk image element from a 9-slice frame, with images for states ("pressed": "button-primary-pressed", ...)."""
    two = IMG.two
    border = P.nine_slice(frame, two)
    args = [IMG.get(f"frames/{frame}")]
    for state, f in (states or {}).items():   # the first matching state wins
        args.append((state, IMG.get(f"frames/{f}")))
    # width/height: the minimum size, which is the image's own size by default (48 px for the input frame, so every
    # combobox was 48 px tall); the content and padding decide instead
    kw = {"border": border, "sticky": "nsew", "width": sum(border[0::2]), "height": sum(border[1::2])}
    if padding is not None:
        kw["padding"] = padding
    try:
        style.element_create(name, "image", *args, **kw)
    except tk.TclError:
        pass   # already created in this interpreter (a rebuild): the images were replaced in place


def apply(root: tk.Tk, name: str, scale: float):
    P.load(name)
    IMG.reset(root, scale >= 1.25)
    try:
        import sv_ttk
        sv_ttk.set_theme("light" if name == "light" else "dark", root)
    except Exception:
        log.exception("sv_ttk not available; using the default theme")
    # sv_ttk's tk_setPalette puts its own background and foreground on every widget through the option database, and
    # on a ttk label that wins over the style: rows of labels showed Sun Valley's grey (#fafafa/#1c1c1c) in the cards
    # It does so on <<ThemeChanged>>, after the window is built, recolouring every widget then; so it runs now, its
    # event binding goes, and its option entries are cleared (plain tk widgets get explicit colours)
    try:
        root.tk.eval("configure_colors")
        root.bind_class("Tk", "<<ThemeChanged>>", "")
    except tk.TclError:
        pass
    root.option_clear()
    for fname, size in BODY_SIZES.items():
        try:
            tkfont.nametofont(fname).configure(size=size)
        except tk.TclError:
            pass
    style = ttk.Style(root)
    root.configure(background=P.window)
    k = 2 if IMG.two else 1
    suffix = f"-{name}"   # element names are per theme: Tk keeps an element once created

    # Base colours
    for cls in ("TFrame", "TLabel", "TCheckbutton", "TRadiobutton", "TLabelframe", "TLabelframe.Label"):
        style.configure(cls, background=P.window, foreground=P.text)
    style.map("TLabel", foreground=[("disabled", P.disabled_text)])
    style.configure("Hint.TLabel", foreground=P.hint)
    style.configure("Title.TLabel", font="SunValleyTitleFont", foreground=P.heading)
    style.configure("Section.TLabel", font="SunValleySubtitleFont", foreground=P.heading)
    style.configure("Turn.TLabel", font="SunValleyBodyStrongFont", foreground=P.your_turn_title)
    style.configure("Pct.TLabel", font=(PIXEL_FONT, 22), foreground=P.heading)
    style.configure("Big.TLabel", font=(PIXEL_FONT, 22), foreground=P.your_turn_title)
    style.configure("Pixel.TLabel", font=(PIXEL_FONT, 12), foreground=P.heading)
    for nm, colour in (("Title.TLabel", P.heading), ("Section.TLabel", P.heading), ("Pct.TLabel", P.heading),
                       ("Big.TLabel", P.your_turn_title), ("Turn.TLabel", P.your_turn_title),
                       ("Hint.TLabel", P.hint), ("Pixel.TLabel", P.heading), ("TLabel", P.text)):
        style.map(nm, foreground=[("disabled", P.disabled_text), ("!disabled", colour)])
    style.configure("Treeview", background=P.card, fieldbackground=P.card, foreground=P.text,
                    rowheight=int(tkfont.nametofont("TkDefaultFont").metrics("linespace") * 1.6))
    style.map("Treeview", background=[("selected", P.selection_bg)], foreground=[("selected", P.selection_text)])
    # Plain areas instead of Sun Valley's images, so the list and its heading take the theme's colours
    for el, src in ((f"Tv.field{suffix}", "Treeview.field"), (f"Th.cell{suffix}", "Treeheading.cell")):
        try:
            style.element_create(el, "from", "clam", src)
        except tk.TclError:
            pass
    style.layout("Treeview", [(f"Tv.field{suffix}", {"sticky": "nswe", "border": "1", "children": [
        ("Treeview.padding", {"sticky": "nswe", "children": [("Treeview.treearea", {"sticky": "nswe"})]})]})])
    style.layout("Treeview.Heading", [(f"Th.cell{suffix}", {"sticky": "nswe"}), ("Treeheading.border", {
        "sticky": "nswe", "children": [("Treeheading.padding", {"sticky": "nswe", "children": [
            ("Treeheading.image", {"side": "right", "sticky": ""}), ("Treeheading.text", {"sticky": "we"})]})]})])
    style.configure("Treeview", bordercolor=P.card_border, lightcolor=P.card, darkcolor=P.card)
    style.configure("Treeview.Heading", background=P.card, foreground=P.heading, bordercolor=P.card_border,
                    lightcolor=P.card, darkcolor=P.card, font="SunValleyBodyStrongFont")
    style.map("Treeview.Heading", background=[("active", P.picker_hover_bg)])

    # Cards: every LabelFrame. The heading sits inside the frame, in the card's colour.
    _element(style, f"Card.border{suffix}", "card")
    _element(style, f"Turn0.border{suffix}", "card-your-turn-0")
    _element(style, f"Turn1.border{suffix}", "card-your-turn-1")
    for st, el in (("TLabelframe", "Card"), ("TurnA.TLabelframe", "Turn0"), ("TurnB.TLabelframe", "Turn1"),
                   ("Blue.TLabelframe", "Card")):
        style.layout(st, [(f"{el}.border{suffix}", {"sticky": "nsew", "children": [
            ("Labelframe.padding", {"expand": "1"})]})])
    # The card's heading stands above the frame (labeloutside); the frame's transparent corners and shadow show
    # the widget's own background, so that is the window colour
    for st in ("TLabelframe", "Blue.TLabelframe"):
        style.configure(st, background=P.window, labeloutside=True, labelmargins=(2 * k, 0, 0, 4 * k))
        style.configure(f"{st}.Label", background=P.window, foreground=P.heading, font="SunValleyBodyStrongFont")
    for st in ("TurnA.TLabelframe", "TurnB.TLabelframe"):
        style.configure(st, background=P.window, labeloutside=True, labelmargins=(2 * k, 0, 0, 4 * k))
        style.configure(f"{st}.Label", background=P.window, foreground=P.your_turn_title,
                        font="SunValleyBodyStrongFont")
    # Card titles inside the card (Card): Segoe UI Semibold 12 pt in heading, your_turn_title on the your-turn card
    style.configure("CardTitle.TLabel", font=("Segoe UI Semibold", 12), foreground=P.heading, background=P.card)
    style.configure("Card.CardTitle.TLabel", background=P.card, foreground=P.heading)
    style.configure("YT.CardTitle.TLabel", background=P.your_turn_card, foreground=P.your_turn_title)
    for nm, colour in (("CardTitle.TLabel", P.heading), ("Card.CardTitle.TLabel", P.heading),
                       ("YT.CardTitle.TLabel", P.your_turn_title)):
        style.map(nm, foreground=[("!disabled", colour)])
    # sv_ttk has its own Card.TFrame (a raised field image); here a frame in a card is just the card's colour
    style.layout("Card.TFrame", style.layout("TFrame"))
    for cls in CARD_CLASSES:
        style.configure(f"Card.{cls}", background=P.card)
        style.configure(f"YT.{cls}", background=P.your_turn_card)
    for nm in ("Hint.TLabel", "Section.TLabel", "Title.TLabel", "Pct.TLabel", "Big.TLabel", "Turn.TLabel",
               "Pixel.TLabel"):
        style.configure(f"Card.{nm}", background=P.card)
        style.configure(f"YT.{nm}", background=P.your_turn_card)

    # Buttons: secondary (TButton) and primary (Accent.TButton), pixel frames with their states
    # 40 px tall with 14 px on the sides inside the frame (the frame's shadow takes 3 px right and below); the main
    # action of a view (Main.Accent.TButton: Start, Klip videoen, Afspil) is 48 px (design review round 1, item 12)
    pad = (14 * k, 9 * k, 17 * k, 12 * k)
    _element(style, f"Btn2.border{suffix}", "button-secondary", {
        "disabled": "button-secondary-disabled", "pressed": "button-secondary-pressed",
        "focus": "button-secondary-focus", "active": "button-secondary-hover"}, padding=0)
    _element(style, f"Btn1.border{suffix}", "button-primary", {
        "disabled": "button-primary-disabled", "pressed": "button-primary-pressed",
        "focus": "button-primary-focus", "active": "button-primary-hover"}, padding=0)
    for st, el, fg in (("TButton", "Btn2", P.button_secondary_text), ("Accent.TButton", "Btn1", P.button_primary_text)):
        style.configure(st, background=P.window)
        style.layout(st, [(f"{el}.border{suffix}", {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})])
        style.configure(st, padding=pad, foreground=fg, anchor="center", font="SunValleyBodyStrongFont")
        style.map(st, foreground=[("disabled", P.disabled_text), ("!disabled", fg)])
    style.configure("Main.Accent.TButton", padding=(18 * k, 12 * k, 21 * k, 16 * k))

    # Check and radio buttons: pixel indicators
    for cls, on, off, extra in (("TCheckbutton", "check-on", "check-off", [("disabled", IMG.get("icons/check-disabled"))]),
                                ("TRadiobutton", "radio-on", "radio-off", [])):
        el = f"{cls[1:]}.pixind{suffix}"
        try:
            # width: the indicator plus 12 px before the text (design review round 1, item 8)
            style.element_create(el, "image", IMG.get(f"icons/{off}"), *extra,
                                 ("selected", IMG.get(f"icons/{on}")), sticky="w",
                                 width=IMG.get(f"icons/{off}").width() + 12 * k)
        except tk.TclError:
            pass
        style.layout(cls, [(f"{cls[1:]}.padding", {"sticky": "nsew", "children": [
            (el, {"side": "left", "sticky": ""}),
            (f"{cls[1:]}.focus", {"side": "left", "sticky": "w", "children": [
                (f"{cls[1:]}.label", {"sticky": "nsew"})]})]})])
        style.configure(cls, padding=(2 * k, 2 * k), indicatormargin=(0, 0, 8 * k, 0))
        style.map(cls, foreground=[("disabled", P.disabled_text), ("!disabled", P.text)])
    for cls in ("TCheckbutton", "TRadiobutton"):
        style.layout(f"Card.{cls}", style.layout(cls))
        style.layout(f"YT.{cls}", style.layout(cls))

    for cls in ("TButton", "Accent.TButton", "Main.Accent.TButton"):
        style.configure(f"Card.{cls}", background=P.card)
        style.configure(f"YT.{cls}", background=P.your_turn_card)
    style.configure("Header.TButton", background=P.header_band)
    # The pickers in the header: the secondary button's frame around the items
    style.layout("Picker.TFrame", [(f"Btn2.border{suffix}", {"sticky": "nsew", "children": [
        ("Frame.padding", {"sticky": "nsew"})]})])
    style.configure("Picker.TFrame", background=P.header_band, padding=(6 * k, 4 * k, 9 * k, 8 * k))
    # One item in a picker: no background, picker-item frames for hover, current and keyboard focus (README "Header")
    hover = IMG.get("frames/picker-item-hover")
    blank = _blank(root, hover)
    try:
        style.element_create(f"PickItem.border{suffix}", "image", blank,
                             ("selected", IMG.get("frames/picker-item-selected")),
                             ("focus", IMG.get("frames/picker-item-focus")), ("active", hover),
                             border=P.nine_slice("picker-item-hover", IMG.two), sticky="nsew")
    except tk.TclError:
        pass
    style.layout("PickItem.TLabel", [(f"PickItem.border{suffix}", {"sticky": "nsew", "children": [
        ("Label.padding", {"sticky": "nsew", "children": [("Label.label", {"sticky": "nsew"})]})]})])
    style.configure("PickItem.TLabel", background=P.button_secondary_bg, foreground=P.button_secondary_text,
                    font=(PIXEL_FONT, -12 * k), padding=(3 * k, 2 * k, 3 * k, 2 * k), anchor="center")
    style.map("PickItem.TLabel", foreground=[("selected", P.picker_selected_text), ("!selected", P.button_secondary_text)])
    # The progress bar's trough (frames/progress-trough) around SegmentBar's canvas
    _element(style, f"Trough.border{suffix}", "progress-trough")
    style.layout("Trough.TFrame", [(f"Trough.border{suffix}", {"sticky": "nsew", "children": [
        ("Frame.padding", {"sticky": "nsew"})]})])
    style.configure("Trough.TFrame", background=P.window)
    # The "your turn" band under the progress bar (frames/callout; design review round 1, item 11)
    _element(style, f"Callout.border{suffix}", "callout")
    style.layout("Callout.TFrame", [(f"Callout.border{suffix}", {"sticky": "nsew", "children": [
        ("Frame.padding", {"sticky": "nsew"})]})])
    style.configure("Callout.TFrame", background=P.window)
    # Settings in the header: the cog alone in a square secondary button
    # 40 x 40 with the 16 px icon centred (the frame's shadow takes 3 px right and below)
    style.configure("Icon.TButton", background=P.header_band, padding=(10 * k, 10 * k, 14 * k, 14 * k))
    # Choice tiles (Programmets sprog, Udseende, the first start): tile frames with hover, chosen and focus
    _element(style, f"Tile.border{suffix}", "tile", {"selected": "tile-selected", "focus": "tile-focus",
                                                    "active": "tile-hover"})
    style.layout("Tile.TFrame", [(f"Tile.border{suffix}", {"sticky": "nsew", "children": [
        ("Frame.padding", {"sticky": "nsew"})]})])
    style.configure("Tile.TFrame", background=P.card, padding=(14 * k, 10 * k, 18 * k, 14 * k))
    style.configure("Tile.Inner.TFrame", background=P.card)
    for nm, font, fg in (("TileCode.TLabel", (PIXEL_FONT, 14), P.heading), ("TileName.TLabel", "SunValleyBodyStrongFont", P.text),
                         ("TileHint.TLabel", "SunValleyCaptionFont", P.hint)):
        style.configure(nm, background=P.card, foreground=fg, font=font)

    # Combobox: the input frame (with a focus state) and the theme's colours, also in its drop-down list
    _element(style, f"Input.field{suffix}", "input", {"focus": "input-focus"})
    try:   # a plain arrow in the text colour (Sun Valley's is a white image)
        style.element_create(f"Pix.downarrow{suffix}", "from", "clam", "Combobox.downarrow")
    except tk.TclError:
        pass
    style.layout("TCombobox", [(f"Input.field{suffix}", {"sticky": "nsew", "children": [
        (f"Pix.downarrow{suffix}", {"side": "right", "sticky": "ns"}),
        ("Combobox.padding", {"expand": "1", "sticky": "nsew", "children": [
            ("Combobox.textarea", {"sticky": "nsew"})]})]})])
    style.configure("TCombobox", padding=(8 * k, 1 * k), foreground=P.text, background=P.input_bg, arrowcolor=P.text,
                    arrowsize=12 * k, bordercolor=P.input_bg, lightcolor=P.input_bg, darkcolor=P.input_bg)
    style.map("TCombobox", fieldbackground=[("readonly", P.input_bg)], foreground=[("readonly", P.text)],
              selectbackground=[("readonly", P.input_bg)], selectforeground=[("readonly", P.text)])
    root.option_add("*TCombobox*Listbox.background", P.input_bg)
    root.option_add("*TCombobox*Listbox.foreground", P.text)
    root.option_add("*TCombobox*Listbox.selectBackground", P.selection_bg)
    root.option_add("*TCombobox*Listbox.selectForeground", P.selection_text)

    # --- Cut review (review.py): the frame around the timeline Canvas (frames/timeline; design review round 1,
    # item 18). Padding: the 2 px border and 4 px air, plus the 4 px shadow on the right and bottom
    _element(style, f"Timeline.border{suffix}", "timeline")
    style.layout("Timeline.TFrame", [(f"Timeline.border{suffix}", {"sticky": "nsew", "children": [
        ("Frame.padding", {"sticky": "nsew"})]})])
    style.configure("Timeline.TFrame", background=P.window, padding=(6 * k, 6 * k, 10 * k, 10 * k))


def _pad4(pad) -> tuple:
    """ttk padding (one, two or four numbers) as (left, top, right, bottom)."""
    p = list(pad) if isinstance(pad, (tuple, list)) else [pad]
    p = [int(x) for x in p]
    if len(p) == 1:
        return (p[0],) * 4
    if len(p) == 2:
        return (p[0], p[1], p[0], p[1])
    if len(p) == 3:
        return (p[0], p[1], p[2], p[1])
    return tuple(p[:4])


class Card(ttk.Frame):
    """A card (frames/card) with its title inside, as the first row (design review round 1, item 7). ttk can only put a
    LabelFrame's label outside or across the frame, so a card is a frame of class TLabelframe (it takes the
    TLabelframe styles and layouts, which have no label) with its title placed in the top padding, which leaves grid
    or pack free for the content. configure(text=...) changes the title."""

    def __init__(self, parent, text: str = "", padding=12, **kw):
        kw.setdefault("style", "TLabelframe")   # ttk takes a frame's style from the widget type, not class_
        super().__init__(parent, class_="TLabelframe", **kw)
        self._pad = _pad4(padding)
        self.title = ttk.Label(self, text=text.strip(), style="CardTitle.TLabel")
        self._layout()

    def _layout(self):
        k = 2 if IMG.two else 1
        left, top, right, bottom = self._pad
        if self.title.cget("text"):
            # Placed from the card's outer edge, where the padding starts too (ttk counts the padding as Tk's internal
            # border, so "inside" would put it at the content's top)
            head = self.title.winfo_reqheight() + 12 * k
            super().configure(padding=(left, top + head, right, bottom))
            self.title.place(x=left, y=top, bordermode="outside")
            self.title.lift()
        else:
            self.title.place_forget()
            super().configure(padding=(left, top, right, bottom))

    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict):
            kw.update(cnf)
            cnf = None
        if "text" in kw:
            self.title.configure(text=str(kw.pop("text")).strip())
            self._layout()
        if "padding" in kw:
            self._pad = _pad4(kw.pop("padding"))
            self._layout()
        if cnf is None and not kw:
            return None
        return super().configure(cnf, **kw)

    config = configure


class IconButton(ttk.Button):
    """A button with a 16 px pixel icon left of its text (design review round 1, item 13). On a primary (yellow)
    button the icon's -on-primary variant is used where there is one, also when the style changes later."""

    def __init__(self, parent, icon: str, **kw):
        self.icon = icon
        super().__init__(parent, compound="left", **kw)
        self._set_icon()

    def _set_icon(self):
        from .theme import THEMES, assets_dir
        name = self.icon
        if "Accent" in str(super().cget("style")):
            if (assets_dir() / "ui" / THEMES[P.name] / "icons" / f"{name}-on-primary.png").exists():
                name += "-on-primary"
        super().configure(image=IMG.get(f"icons/{name}"))

    def configure(self, cnf=None, **kw):
        out = super().configure(cnf, **kw)
        if "style" in kw or (isinstance(cnf, dict) and "style" in cnf):
            self._set_icon()
        return out

    config = configure


def _blank(root, like: tk.PhotoImage) -> tk.PhotoImage:
    """A transparent image the size of like (a picker item without a frame), kept referenced with the theme's images."""
    img = tk.PhotoImage(master=root, width=like.width(), height=like.height())
    IMG._cache[f"blank-{like.width()}x{like.height()}"] = img
    return img


def skin_cards(widget):
    """Give every ttk frame, label and check/radio button inside a card the card's background: "Hint.TLabel" in a card
    becomes "Card.Hint.TLabel". Run after building (and after adding widgets to a card)."""
    def walk(w, prefix):
        for child in w.winfo_children():
            cls = child.winfo_class()
            p = prefix
            if cls == "TLabelframe":
                st = str(child.cget("style") or "")
                p = "YT." if st.startswith("Turn") else "Card."   # YT: the your-turn card
            elif prefix and cls in CARD_CLASSES:
                try:
                    st = str(child.cget("style") or "")
                    base = st.removeprefix("Card.").removeprefix("YT.") or cls
                    child.configure(style=prefix + base)
                except tk.TclError:
                    pass
            elif prefix and cls in ("Canvas", "Label", "Frame", "Listbox"):
                try:
                    child.configure(background=P.card if prefix == "Card." else P.your_turn_card)
                except tk.TclError:
                    pass
            walk(child, p)
    walk(widget, "")


def dialog(win):
    """A dialog (Toplevel) in the current theme: background, cards, and a dark title bar in Nat."""
    from .theme import dark_title_bar
    try:
        win.configure(background=P.window)
    except tk.TclError:
        pass
    skin_cards(win)
    win.after(30, lambda: dark_title_bar(win, P.name == "night"))
