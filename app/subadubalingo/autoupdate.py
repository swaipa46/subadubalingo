"""The self-update in the window (docs/auto-opdatering.md, section 4); the network, checks and install are update.py.

On start, in the installed copy only and at most once a day, a background thread fetches the signed manifest, and
when there is a newer version downloads and stages it next to the current one (update.run). Offline or any other
trouble: quiet, a line in the log. The download waits while a video is being made.

When a version is staged, a band at the bottom of the window says so ("Ny version 2.1.0 er klar · Genstart for at
opdatere") with "Genstart nu", which switches current.txt, shortcuts and Claude's entry to it and starts it
(update.switch/restart), and "Senere". It is not a popup, so nothing is interrupted, and restarting is not possible
while a video is being made. The new copy, started with --updated-from, says "Opdateret til version 2.1.0" once.

The band is the "din tur" callout frame (frames/callout) in the theme's callout colours. App._build calls build(),
so it comes back after a theme or language switch.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

from . import theme, update
from .common import APP_VERSION, log
from .i18n import t
from .pickers import Tooltip
from .theme import P

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
        while app._running or app._starting:   # no download while a video is being made
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
    return bool(app._running or app._starting)


def build(app):
    """The band at the bottom of the main window (row 2 of app.main), or nothing. Safe to call again."""
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
    style = ttk.Style(app.root)
    style.configure("Update.TButton", background=P.callout_bg)
    style.configure("Update.Accent.TButton", background=P.callout_bg)
    f = st.frame = ttk.Frame(app.main, style="Callout.TFrame",
                             padding=(int(16 * k), int(8 * k), int(18 * k), int(10 * k)))
    f.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(int(14 * k), 0))
    f.columnconfigure(1, weight=1)
    label = dict(background=P.callout_bg, foreground=P.callout_text)
    tk.Label(f, text=t("autoupdate.tag"), font=(theme.PIXEL_FONT, -int(10 * k)), **label).grid(
        row=0, column=0, sticky="w", padx=(int(4 * k), int(16 * k)))
    text = tk.Label(f, font="SunValleyBodyStrongFont", justify="left", anchor="w", **label)
    text.grid(row=0, column=1, sticky="ew")
    f.bind("<Configure>", lambda e: text.configure(wraplength=max(200, e.width - int(420 * k))), add="+")
    buttons = tk.Frame(f, background=P.callout_bg)
    buttons.grid(row=0, column=2, sticky="e", padx=(int(12 * k), 0))
    if kind == "updated":
        text.configure(text=t("autoupdate.updated", version=APP_VERSION, old=st.updated_from))
        ttk.Button(buttons, text=t("autoupdate.ok"), style="Update.TButton",
                   command=lambda: _close_updated(app)).pack(side="left")
        return
    version = st.ready["version"]
    notes = str(st.ready.get("notes") or "").strip()
    if notes:
        Tooltip(text, notes)
    later = ttk.Button(buttons, text=t("autoupdate.later"), style="Update.TButton", command=lambda: _later(app))
    later.pack(side="left", padx=(0, int(8 * k)))
    go = ttk.Button(buttons, text=t("autoupdate.restart"), style="Update.Accent.TButton",
                    command=lambda: restart_now(app))
    go.pack(side="left")

    def refresh():
        if st.frame is not f or not f.winfo_exists():
            return
        busy = _busy(app)
        if st.error:
            text.configure(text=st.error)
        else:
            text.configure(text=t("autoupdate.ready_busy" if busy else "autoupdate.ready", version=version))
        go.configure(state="disabled" if busy else "normal")
        app.root.after(1000, refresh)
    refresh()


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
