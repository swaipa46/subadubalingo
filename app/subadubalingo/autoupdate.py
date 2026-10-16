"""The self-update in the window (docs/auto-opdatering.md, section 4); the network, checks and install are update.py.

On start, in the installed copy only and at most once a day, a background thread fetches the signed manifest, and
when there is a newer version downloads and stages it next to the current one (update.run). Offline or any other
trouble: quiet, a line in the log. The download waits while a video is being made.

When a version is staged, a band under the header says so ("NY VERSION 2.1.0 er klar · Genstart for at opdatere.
Dine indstillinger bevares.") with "Genstart nu", which switches current.txt, shortcuts and Claude's entry to it and
starts it (update.switch/restart), "Senere" and x. It is not a popup, so nothing is interrupted, and restarting is not
possible while a video is being made. The new copy, started with --updated-from, says "LEVEL UP Version 2.1.0" once.

Design: design/mockups/*/update-banner.png and assets/ui/README.md (T8): frames/update-banner (the "din tur" callout
frame where a theme lacks it) in the callout colours. App._build calls build(), so it comes back after a theme or
language switch.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

from . import editor, skin, theme, update
from .common import APP_VERSION, log
from .i18n import t
from .pickers import Tooltip
from .theme import IMG, P

DAY = 24 * 3600
URL_ENV = "SUBADUBALINGO_UPDATE_URL"   # another manifest URL (testing); the signature is checked all the same


class State:
    def __init__(self):
        self.ready: dict | None = None        # the staged update ({"version", "notes"})
        self.updated_from: str | None = None  # this copy was just started by an update from that version
        self.dismissed = False                # "Senere" for this session
        self.error = ""
        self.frame = None
        self.thread: threading.Thread | None = None
        self.result = None


def _state(app) -> State:
    if not hasattr(app, "_autoupdate"):
        app._autoupdate = State()
    return app._autoupdate


def _updated_from(argv) -> str | None:
    if "--updated-from" in argv:
        i = argv.index("--updated-from")
        return argv[i + 1] if i + 1 < len(argv) else "?"
    return None


def should_check(enabled: bool, installed: bool, last: float, now: float, forced_url: str | None) -> bool:
    """Whether to ask GitHub now: only the installed copy (or a test URL), only when the setting is on, and at
    most once a day."""
    return bool(enabled and (installed or forced_url) and now - (last or 0) >= DAY)


def start(app):
    """Called once from App.__init__ after the window is built."""
    from . import installer
    st = _state(app)
    st.updated_from = _updated_from(sys.argv)
    if st.updated_from == APP_VERSION:
        st.updated_from = None
    s = app.settings
    url = os.environ.get(URL_ENV)
    installed = installer.running_installed()
    if getattr(s, "check_updates", True) and (installed or url):
        st.ready = update.ready()
    if st.ready or st.updated_from:
        app.root.after(800, lambda: build(app))
    if st.ready or not should_check(getattr(s, "check_updates", True), installed, getattr(s, "update_checked", 0),
                                    time.time(), url):
        return

    def wait_idle():
        while _busy(app):   # no download while a video (or a queue of them) is being made
            time.sleep(15)

    def work():
        st.result = update.run(url or update.MANIFEST_URL, APP_VERSION, wait_idle)

    st.thread = threading.Thread(target=work, daemon=True, name="update")
    st.thread.start()
    app.root.after(2000, lambda: _poll(app))


def _poll(app):
    st = _state(app)
    if st.thread and st.thread.is_alive():
        app.root.after(2000, lambda: _poll(app))
        return
    status, info = st.result or ("offline", None)
    st.thread = None
    if status != "offline":   # offline: try again at the next start
        app.settings.update_checked = time.time()
        app.settings.save()
    if status == "ready" and info:
        st.ready = info
        build(app)


def _busy(app) -> bool:
    """A video is being made, or a queue of them is running (also between its rounds, see queue_view)."""
    vq = getattr(app, "vq", None)
    return bool(app._running or app._starting or (vq is not None and vq.running))


def build(app):
    """The band under the header (row 0 of app.main), or nothing. Safe to call again."""
    st = _state(app)
    try:
        if st.frame is not None and st.frame.winfo_exists():
            st.frame.destroy()
    except tk.TclError:
        pass
    st.frame = None
    if not getattr(app, "main", None) or not app.main.winfo_exists():
        return
    if st.ready and not st.dismissed:
        kind = "ready"
    elif st.updated_from:
        kind = "updated"
    else:
        return
    k = app.scale
    f = st.frame = ttk.Frame(app.main, style=_band_style(app),
                             padding=(int(16 * k), int(6 * k), int(12 * k), int(6 * k)))
    f.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, int(16 * k)))
    f.columnconfigure(3, weight=1)
    f.rowconfigure(0, minsize=int(34 * k))   # as tall with or without buttons
    label = dict(background=P.callout_bg, foreground=P.callout_text)
    up = _image("icons/up-on-callout")
    if up:
        tk.Label(f, image=up, borderwidth=0, **label).grid(row=0, column=0, padx=(0, int(12 * k)))
    tag = tk.Label(f, font=(theme.PIXEL_FONT, -int(12 * k)), **label)
    tag.grid(row=0, column=1, sticky="w", padx=(0, int(16 * k)), pady=(int(3 * k), 0))   # the pixel font sits high
    strong = tk.Label(f, font="SunValleyBodyStrongFont", **label)
    strong.grid(row=0, column=2, sticky="w", padx=(0, int(8 * k)))
    # One line, clipped in a narrow window (wrapping on the band's width fed back into the layout and never settled)
    rest = tk.Label(f, font="SunValleyBodyFont", anchor="w", **label)
    rest.grid(row=0, column=3, sticky="ew")

    buttons = tk.Frame(f, background=P.callout_bg)
    buttons.grid(row=0, column=4, sticky="e", padx=(int(12 * k), 0))
    close = tk.Label(buttons, text="✕", cursor="hand2", font="SunValleyBodyStrongFont", padx=int(8 * k), **label)
    if kind == "updated":
        tag.configure(text=t("autoupdate.updated_tag"))
        strong.configure(text=t("autoupdate.updated", version=APP_VERSION))
        rest.configure(text=t("autoupdate.updated_hint", old=st.updated_from))
        close.bind("<Button-1>", lambda e: _close_updated(app))
        close.pack(side="left", padx=(int(8 * k), 0))
        Tooltip(close, t("autoupdate.close"))
        return
    version = st.ready["version"]
    tag.configure(text=t("autoupdate.tag"))
    strong.configure(text=t("autoupdate.version", version=version))
    notes = str(st.ready.get("notes") or "").strip()
    if notes:
        Tooltip(rest, notes)
    # Primary on Lys; on Nat the band is yellow, so a secondary button (assets/ui/README.md, update banner)
    go = ttk.Button(buttons, text=t("autoupdate.restart"), command=lambda: restart_now(app),
                    style="Update.Accent.TButton" if P.name == "light" else "Update.TButton")
    go.pack(side="left", padx=(0, int(8 * k)))
    Tooltip(go, lambda: t("autoupdate.busy_tip") if _busy(app) else t("autoupdate.restart_tip"))
    ttk.Button(buttons, text=t("autoupdate.later"), style="Update.TButton",
               command=lambda: _later(app)).pack(side="left")
    close.bind("<Button-1>", lambda e: _later(app))
    close.pack(side="left", padx=(int(8 * k), 0))
    Tooltip(close, t("autoupdate.later"))

    def refresh():
        if st.frame is not f or not f.winfo_exists():
            return
        busy = _busy(app)
        rest.configure(text=st.error or t("autoupdate.hint"))
        go.configure(state="disabled" if busy else "normal")
        app.root.after(1000, refresh)
    refresh()


def _image(rel: str):
    try:
        return IMG.get(rel) if IMG.path(rel).exists() else None
    except Exception:
        return None


def _band_style(app) -> str:
    """The band's frame: frames/update-banner (design T8), or the "din tur" callout frame where the theme lacks it.
    Smaller buttons than elsewhere, on the band's colour."""
    style = ttk.Style(app.root)
    k = app.scale
    for name in ("Update.TButton", "Update.Accent.TButton"):
        style.configure(name, background=P.callout_bg, padding=(12 * k, 5 * k, 15 * k, 8 * k))
    try:
        if not _image("frames/update-banner"):
            raise KeyError("update-banner")
        P.nine_slice("update-banner", IMG.two)
        element = f"UpdateBanner.border-{P.name}"
        skin._element(style, element, "update-banner")
        style.layout("UpdateBanner.TFrame", [(element, {"sticky": "nsew", "children": [
            ("Frame.padding", {"sticky": "nsew"})]})])
        style.configure("UpdateBanner.TFrame", background=P.window)
        return "UpdateBanner.TFrame"
    except (KeyError, tk.TclError):
        return "Callout.TFrame"


def _later(app):
    st = _state(app)
    st.dismissed = True
    build(app)


def _close_updated(app):
    st = _state(app)
    st.updated_from = None
    build(app)


def restart_now(app):
    """Switch to the staged version and start it; this window closes. Never while a video is being made."""
    st = _state(app)
    if not st.ready or _busy(app):
        return
    if not editor.may_close_app(app):   # unsaved subtitle edits, and the user chose to stay
        return
    app.root.configure(cursor="watch")
    app.root.update_idletasks()
    try:
        exe = update.switch(st.ready["version"])
    except Exception as e:
        log.exception("update switch failed")
        st.error = t("autoupdate.failed", error=e)
        app.root.configure(cursor="")
        return
    log.info("restarting into %s", exe)
    _release_single_instance()
    update.restart(exe, APP_VERSION)
    app.root.destroy()


def _release_single_instance():
    """Let the new copy start: it checks the single-instance mutex that gui.main() holds."""
    try:
        from . import gui
        handle = getattr(gui.main, "mutex", None)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            gui.main.mutex = None
    except Exception:
        log.exception("could not release the single-instance mutex")
