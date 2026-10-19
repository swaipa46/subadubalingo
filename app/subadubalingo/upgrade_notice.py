"""The one-time "delete the old skill" notice after an update (docs/assistant-panel.md, "Upgrade").

Every version up to 2.1.x shipped a Claude skill ("subadubalingo", earlier "videosprog") that users uploaded to
Claude. Left in Claude, it tells Claude about task files and a protocol the app no longer has. Users who come from
such a version are asked once, right after the update, to delete it; fresh installs never see it.

The decision is pure (needs_old_skill_notice, decide_at_start) so it is tested on any OS; the dialog imports tkinter
only when it is shown.
"""
from __future__ import annotations

from .common import APP_NAME, APP_VERSION, log

SKILL_REMOVED_IN = "2.2.0"   # the first version without the skill (the APP_VERSION this ships in)
OLD_SKILL_NAMES = ("subadubalingo", "videosprog")


def _vtuple(v: str | None) -> tuple:
    try:
        return tuple(int(x) for x in (v or "0").split("."))
    except ValueError:
        return (0,)


def needs_old_skill_notice(saved: dict | None) -> bool:
    """saved: settings.json as read at start, None when there was no file (a fresh install).

    The app of 2.1.x and older wrote every setting, "skill_installed" among them (0 meant "unknown", not "absent":
    the skill could have been saved from Indstillinger), and no "last_version". The installer's first-start page
    writes only "ui_language" before the app's first start, so a file without either key is a fresh install too."""
    if not saved or saved.get("old_skill_notice_done"):
        return False
    if saved.get("old_skill_notice_pending"):
        return True
    if (saved.get("skill_installed") or 0) > 0:
        return True
    last = saved.get("last_version")
    if last:
        return _vtuple(last) < _vtuple(SKILL_REMOVED_IN)
    return "skill_installed" in saved


def decide_at_start(settings, from_assistant: bool) -> bool:
    """Run once at every start, before anything else saves the settings. Records this version, marks a fresh install
    as done, and keeps a needed notice pending until it has been shown (a session an assistant started does not
    show it: the user is mid-chat, and the server's instructions already tell the assistant to mention it).
    Returns whether to show the notice in this session. Saves the settings."""
    needed = needs_old_skill_notice(getattr(settings, "_saved", None))
    settings.last_version = APP_VERSION
    if needed:
        settings.old_skill_notice_pending = True
    else:
        settings.old_skill_notice_done = True
        settings.old_skill_notice_pending = False
    settings.save()
    return needed and not from_assistant


def mark_shown(settings):
    """Saved the moment the notice is shown (not on close), so a crash or a kill cannot make it show twice."""
    settings.old_skill_notice_done = True
    settings.old_skill_notice_pending = False
    settings.save()


def show(app):
    """The notice on the main window: not modal, one dialog at a time (app._notice)."""
    import tkinter as tk
    from tkinter import ttk

    from . import gui
    from .i18n import t
    from .theme import P

    if app._notice is not None:
        try:
            if app._notice.winfo_exists():
                return
        except tk.TclError:
            pass
    root = app.root
    win = tk.Toplevel(root)
    win.withdraw()   # shown by place_over, so it never flashes in Windows' default spot
    heading = t("oldskill.heading")
    win.title(heading)
    win.transient(root)
    win.resizable(False, False)
    f = ttk.Frame(win, padding=18)
    f.pack(fill="both", expand=True)
    wrap = int(520 * app.scale)
    ttk.Label(f, text=heading, style="Section.TLabel", foreground=P.heading).pack(anchor="w")
    ttk.Label(f, text=t("oldskill.intro", app=APP_NAME), wraplength=wrap, justify="left").pack(anchor="w", pady=(6, 12))
    names = f" {t('gui.and')} ".join(f'"{n}"' for n in OLD_SKILL_NAMES)
    for i, step in enumerate((t("oldskill.step_open"), t("oldskill.step_delete", names=names)), 1):
        ttk.Label(f, text=f"{i}.  {step}", wraplength=wrap, justify="left").pack(anchor="w", pady=2)
    ttk.Label(f, text=t("oldskill.no_claude"), style="Hint.TLabel", wraplength=wrap, justify="left").pack(
        anchor="w", pady=(10, 0))
    ok = ttk.Button(f, text=t("oldskill.ok"), style="Accent.TButton", command=win.destroy, default="active")
    ok.pack(anchor="w", pady=(14, 0))
    win.close = lambda e=None: win.destroy()   # also what the test calls: a key event needs focus, which CI lacks
    win.bind("<Escape>", win.close)
    win.bind("<Return>", win.close)
    app._notice = win
    gui.place_over(win, root)
    ok.focus_set()
    try:
        mark_shown(app.settings)
    except Exception:
        log.exception("could not save that the old-skill notice was shown")
