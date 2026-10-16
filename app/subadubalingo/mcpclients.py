"""Indstillinger's card "Brug med andre AI-værktøjer": the setup to copy for each AI tool that can start a local MCP
server, and the setups themselves (also used by the docs and tests/test_mcp_clients.py).

Every client but Claude desktop points at the stable launcher (installer.launcher_path(): SubaDubaLingo-mcp.exe in
the install folder, tools/mcp_launcher.cs), which runs the current version: their configs are the user's to edit, so
they must survive an update. Claude desktop keeps the button "Forbind med Claude", which writes its config with the
versioned exe as before; the installer rewrites that entry at every install and update, so it needs no launcher, and
it saves Claude desktop a process and a console-less launcher in between.

The formats were checked against each client's documentation on 2026-10-16 (the URLs are by each snippet). Clients
other than Claude (desktop app, and Claude Code through the launcher) are not tested with the app yet
(docs/mcp-clients.md).
"""
from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from . import skin
from .i18n import t
from .theme import IMG, P

SERVER = "subadubalingo"   # the server's name in every config (installer.MCP_NAME)
# key, tile code, name (never translated), tested with the app
CLIENTS = (("claude_desktop", "CD", "Claude", True),
           ("claude_code", "CC", "Claude Code", True),
           ("gemini", "GE", "Gemini CLI", False),   # 16.10.2026: the login was refused (docs/mcp-clients.md)
           ("codex", "CX", "Codex CLI", False),
           ("vscode", "VS", "VS Code", False),
           ("cursor", "CU", "Cursor", False))
KEYS = tuple(c[0] for c in CLIENTS)
COPIED_MS = 2000


def _json(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _toml_str(s: str) -> str:
    """A TOML string: a literal one ('...', no escapes: the backslashes stay as they are) unless it holds a quote."""
    if "'" not in s and "\n" not in s:
        return f"'{s}'"
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def snippet(key: str, launcher: Path, exe: Path | None = None) -> str:
    """The setup for one client, to paste where where_text() says. launcher: the stable launcher; exe: the versioned
    exe (Claude desktop only, as "Forbind med Claude" writes it)."""
    cmd = str(launcher)
    if key == "claude_desktop":
        # %APPDATA%\Claude\claude_desktop_config.json; installer.point_claude_at writes the same
        # https://modelcontextprotocol.io/docs/develop/connect-local-servers
        return _json({"mcpServers": {SERVER: {"command": str(exe or launcher), "args": ["--mcp"] if exe else []}}})
    if key == "claude_code":
        # https://code.claude.com/docs/en/mcp : claude mcp add [options] <name> -- <command> [args...]; user scope =
        # every project, stored in ~/.claude.json
        return f'claude mcp add --transport stdio --scope user {SERVER} -- "{cmd}"'
    if key == "gemini":
        # https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md : "mcpServers" in
        # ~/.gemini/settings.json (or .gemini/settings.json in a project); no "type"; the tool timeout is 10 min
        return _json({"mcpServers": {SERVER: {"command": cmd, "args": []}}})
    if key == "codex":
        # https://learn.chatgpt.com/docs/extend/mcp?surface=cli and .../config-file/config-reference :
        # [mcp_servers.<name>] in ~/.codex/config.toml; the tool timeout is raised for start_video/wait (~45 s)
        return (f"[mcp_servers.{SERVER}]\ncommand = {_toml_str(cmd)}\nargs = []\nstartup_timeout_sec = 30\n"
                "tool_timeout_sec = 120\n")
    if key == "vscode":
        # https://code.visualstudio.com/docs/copilot/reference/mcp-configuration : "servers" in .vscode/mcp.json or
        # the user mcp.json (MCP: Open User Configuration); "type": "stdio"
        return _json({"servers": {SERVER: {"type": "stdio", "command": cmd, "args": []}}})
    if key == "cursor":
        # https://cursor.com/docs/context/mcp : "mcpServers" in ~/.cursor/mcp.json (or .cursor/mcp.json in a project)
        return _json({"mcpServers": {SERVER: {"type": "stdio", "command": cmd, "args": []}}})
    raise KeyError(key)


def snippets(launcher: Path, exe: Path | None = None) -> dict[str, str]:
    return {key: snippet(key, launcher, exe) for key in KEYS}


def _styles():
    """An input frame (the element skin.apply made for frames/input) around the setup, in and outside a card."""
    style = ttk.Style()
    try:
        field = style.layout("TCombobox")[0][0]
        for name in ("Input.Snippet.TFrame", "Card.Input.Snippet.TFrame"):
            style.layout(name, [(field, {"sticky": "nsew", "children": [("Frame.padding", {"sticky": "nsew"})]})])
            style.configure(name, background=P.card)
    except (tk.TclError, IndexError):
        pass


class ClientsCard(skin.Card):
    """The card: the clients as tiles, then for the chosen one where to paste, the setup and Kopiér (for Claude
    desktop the button "Forbind med Claude" instead)."""

    last = "claude_code"   # the client shown last, for as long as the program runs (as Indstillinger's open card)

    def __init__(self, parent, launcher: Path, on_connect, installed: bool = True):
        super().__init__(parent, text=t("mcpclients.title"), padding=12)
        from .pickers import Tiles
        k = 2 if IMG.two else 1
        self.k, self.launcher, self.on_connect = k, launcher, on_connect
        self.choice = tk.StringVar(value=ClientsCard.last)
        # The texts wrap at a fixed width (wrapping at the card's own width would shrink it step by step), about the
        # width the footer gives Indstillinger
        wrap = 600 * k
        self.intro = ttk.Label(self, text=t("mcpclients.intro"), style="Hint.TLabel", justify="left", wraplength=wrap)
        self.intro.pack(anchor="w", fill="x", pady=(0, 10 * k))
        Tiles(self, self.choice, [(key, code, None, name, t(f"mcpclients.{key}.hint")) for key, code, name, _ in CLIENTS],
              columns=3, on_change=self._show).pack(fill="x")
        self.where = ttk.Label(self, text="", justify="left", wraplength=wrap)
        self.where.pack(anchor="w", fill="x", pady=(4 * k, 8 * k))
        # The setup in the input frame of the design system (frames/input, as the burn-in corner picker), read-only
        _styles()
        self.box = ttk.Frame(self, style="Input.Snippet.TFrame", padding=(8 * k, 7 * k))
        self.text = tk.Text(self.box, height=4, width=10, wrap="char", relief="flat", borderwidth=0,
                            padx=0, pady=0, font=("Consolas", 9), background=P.input_bg, foreground=P.text,
                            selectbackground=P.selection_bg, selectforeground=P.selection_text, highlightthickness=0,
                            cursor="arrow")
        self.text.keep_colour = True   # skin_cards: the input's colour, not the card's
        self.text.pack(fill="both", expand=True)
        self.text.bind("<Configure>", lambda e: self._fit())
        self.acts = ttk.Frame(self)
        self.copy_btn = skin.IconButton(self.acts, "copy", text=t("mcpclients.copy"), command=self._copy)
        self.connect_btn = ttk.Button(self.acts, text=t("gui.settings.connect"), command=on_connect)
        self.status = ttk.Label(self.acts, text="", style="Hint.TLabel")
        self.note = ttk.Label(self, text=t("mcpclients.not_installed") if not installed else "", style="Hint.TLabel",
                              justify="left", wraplength=wrap)
        self._show(self.choice.get())

    def _show(self, key: str):
        ClientsCard.last = key
        tested = next(c[3] for c in CLIENTS if c[0] == key)
        self.where.configure(text=t(f"mcpclients.{key}.where"))
        for w in (self.box, self.acts, self.note):
            w.pack_forget()
        for w in (self.copy_btn, self.connect_btn, self.status):
            w.pack_forget()
        if key == "claude_desktop":
            self.connect_btn.pack(side="left")
        else:
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.insert("1.0", snippet(key, self.launcher).rstrip("\n"))
            self.text.configure(state="disabled")
            self.box.pack(fill="x")
            self.copy_btn.configure(text=t("mcpclients.copy"))
            self.copy_btn.pack(side="left")
            self._fit()
        self.status.configure(text=t("mcpclients.tested" if tested else "mcpclients.untested"))
        self.status.pack(side="left", padx=(14 * self.k, 0))
        self.acts.pack(anchor="w", fill="x", pady=(10 * self.k, 0))
        if self.note.cget("text") and key != "claude_desktop":
            self.note.pack(anchor="w", fill="x", pady=(8 * self.k, 0))

    def _fit(self):
        """As many rows as the setup takes at the box's width (it wraps long paths)."""
        try:
            rows = self.text.count("1.0", "end", "displaylines")
        except tk.TclError:
            return
        rows = (rows[0] if isinstance(rows, tuple) else rows) or 1
        if int(self.text.cget("height")) != rows:
            self.text.configure(height=rows)

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(snippet(self.choice.get(), self.launcher))
        self.update()
        self.copy_btn.configure(text=t("mcpclients.copied"))
        self.after(COPIED_MS, lambda: self.copy_btn.winfo_exists() and self.copy_btn.configure(text=t("mcpclients.copy")))
