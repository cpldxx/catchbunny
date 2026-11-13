"""`catchbunny connect`: point the coding agents on this machine at Catchbunny.

By default an agent is pointed at the MCP endpoint of the Catchbunny that
`catchbunny start` is running, http://127.0.0.1:8700/mcp: one server, so a scan an
agent starts shows in the console as it happens. With `--stdio` the agent starts a
Catchbunny of its own instead, and no server needs to be running.

Each agent keeps its servers in a file of its own, in a format of its own:

    claude-code     `claude mcp add-json --scope user` (every project), or ./.mcp.json (--scope project)
    codex           ~/.codex/config.toml  [mcp_servers.catchbunny]   (the CLI, the IDE extension and the desktop app share it)
    claude-desktop  claude_desktop_config.json  "mcpServers"  (commands only, so always --stdio)

This writes one entry named `catchbunny`, leaves everything else in the file as it
was, keeps a `.bak` of what was there, and can take the entry out again. Connectors
that reach Catchbunny over the internet (Claude and ChatGPT on the web) are set up
in those products: see the console's Connect page on a team server.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

NAME = "catchbunny"
CLIENTS = ("claude-code", "codex", "claude-desktop")
AUTO = ("claude-code", "codex")  # what `catchbunny connect` with no names connects, when it finds them
DEFAULT_URL = "http://127.0.0.1:8700/mcp"  # where `catchbunny start` serves MCP


def launch() -> list[str]:
    """The command that starts the MCP server, as an agent should run it.

    Always an absolute path: an app started from the dock does not have the shell's PATH,
    and a shell that has `catchbunny` on it today (inside `uv run`, or an activated
    environment) may not tomorrow. A `.mcp.json` meant to be committed for a team is
    better written by hand as {"command": "catchbunny", "args": ["mcp"]}.
    """
    script = Path(sys.executable).parent / NAME
    return [str(script), "mcp"] if script.exists() else [sys.executable, "-m", NAME, "mcp"]


@dataclass
class Change:
    """One thing to do for one agent: a file to write, or a command of the agent's own to run.

    Built first and applied second, so `--print` shows exactly what a real run would do.
    """

    done: str  # what to say once it is applied
    path: Path | None = None
    text: str | None = None  # the file's new contents; None when it is already as it should be
    argv: list[str] | None = None
    entry: str = ""  # our part of the file, which is all that is ever shown: the rest is the user's

    def describe(self) -> str:
        if self.argv:
            return "would run: " + " ".join(_quote(a) for a in self.argv)
        if self.text is None:
            return f"nothing to change in {self.path}"
        return f"would {'add to' if self.entry else 'remove its entry from'} {self.path}" + (f"\n{self.entry}" if self.entry else "")

    def apply(self) -> str:
        if self.argv:
            result = subprocess.run(self.argv, capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(f"`{' '.join(self.argv[:3])}` failed: {(result.stderr or result.stdout).strip()}")
            return self.done
        if self.text is None or self.path is None:
            return self.done
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
        scratch = self.path.with_name(self.path.name + ".tmp")
        scratch.write_text(self.text)
        os.replace(scratch, self.path)  # the agent may be reading the file: it sees the old one or the new one, never half
        return self.done


def _quote(arg: str) -> str:
    return arg if re.fullmatch(r"[\w@%+=:,./-]+", arg) else "'" + arg.replace("'", "'\\''") + "'"


def _json_entry(path: Path, key: str, entry: dict[str, object] | None) -> str | None:
    """`path` with our entry set under `key` (or removed, when entry is None). None when there is nothing to change."""
    doc: dict[str, object] = {}
    if path.exists() and path.read_text().strip():
        loaded = json.loads(path.read_text())
        if not isinstance(loaded, dict):
            raise ValueError(f"{path} is not a JSON object")
        doc = loaded
    servers = doc.get(key)
    if not isinstance(servers, dict):
        servers = {}
    if entry is None:
        if NAME not in servers:
            return None
        del servers[NAME]
    else:
        if servers.get(NAME) == entry:
            return None
        servers[NAME] = entry
    doc[key] = servers
    return json.dumps(doc, indent=2) + "\n"


def _shown(key: str, entry: dict[str, object]) -> str:
    return json.dumps({key: {NAME: entry}}, indent=2)


_TABLE = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")


def _toml_entry(path: Path, block: str | None) -> str | None:
    """`path` with our [mcp_servers.catchbunny] table set to `block` (or without one, when block is None).

    Edited as text: the file is the user's, with their comments and ordering, and a
    rewrite through a TOML library would lose both. Only our own table is touched.
    """
    before = path.read_text() if path.exists() else ""
    tomllib.loads(before)  # refuse to edit a file that is already broken
    kept: list[str] = []
    ours = False
    for line in before.splitlines():
        if match := _TABLE.match(line):
            table = match.group(1).replace(" ", "").replace('"', "")
            ours = table == f"mcp_servers.{NAME}" or table.startswith(f"mcp_servers.{NAME}.")
        if not ours:
            kept.append(line)
    text = "\n".join(kept).rstrip("\n")
    if block is not None:
        text = (text + "\n\n" if text else "") + block
    text = text + "\n" if text else ""
    tomllib.loads(text)
    return None if text == before else text


def _toml_block(url: str | None) -> str:
    """Our table: the endpoint of a running Catchbunny, or (url None) the command that starts one."""
    if url:
        where = [f"url = {json.dumps(url)}"]
    else:
        command = launch()
        where = [
            f"command = {json.dumps(command[0])}",
            f"args = {json.dumps(command[1:])}",
            "startup_timeout_sec = 30",  # starting up takes longer than the 10 seconds allowed by default
        ]
    # scan answers within 45 seconds and is polled; this is headroom, not the mechanism
    return "\n".join([f"[mcp_servers.{NAME}]", *where, "tool_timeout_sec = 120"])


def _stdio() -> dict[str, object]:
    command = launch()
    return {"command": command[0], "args": command[1:]}


def claude_code(scope: str, remove: bool, cwd: Path, url: str | None) -> Change:
    if not scope:
        scope = "user" if shutil.which("claude") else "project"  # once for every project, when its own CLI can do that
    if scope == "project":
        path = cwd / ".mcp.json"
        entry = {"type": "http", "url": url} if url else _stdio()
        text = _json_entry(path, "mcpServers", None if remove else entry)
        if remove:
            return Change(f"claude-code     removed from {path}" if text else f"claude-code     was not in {path}", path, text)
        return Change(f"claude-code     {path} (this project). Start a new session in this folder and approve the server when asked.", path, text, entry=_shown("mcpServers", entry))
    entry = json.dumps({"type": "http", "url": url} if url else {"type": "stdio", **_stdio()})
    argv = ["claude", "mcp", "remove", NAME, "--scope", "user"] if remove else ["claude", "mcp", "add-json", NAME, entry, "--scope", "user"]
    if shutil.which("claude") is None:
        raise RuntimeError("the `claude` command is not on PATH, and user scope is set through it. Use --scope project, or run:\n  " + " ".join(_quote(a) for a in argv))
    return Change(f"claude-code     {'removed' if remove else 'added'} for every project. Start a new Claude Code session to use it.", argv=argv)


def codex(scope: str, remove: bool, cwd: Path, url: str | None) -> Change:
    path = (cwd / ".codex" if scope == "project" else Path.home() / ".codex") / "config.toml"
    block = _toml_block(url)
    text = _toml_entry(path, None if remove else block)
    if remove:
        return Change(f"codex           removed from {path}" if text is not None else f"codex           was not in {path}", path, text)
    return Change(f"codex           {path}. Restart Codex: its CLI, IDE extension and desktop app read the same file.", path, text, entry=block)


def claude_desktop_config() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Claude" / "claude_desktop_config.json"


def _claude_is_open() -> bool:
    if sys.platform != "darwin":
        return False
    # Not pgrep: it leaves out its own ancestors, and when an agent runs this command the app is one of them.
    listing = subprocess.run(["ps", "-Ao", "comm="], capture_output=True, text=True).stdout
    return any(line.strip().endswith("/Claude.app/Contents/MacOS/Claude") for line in listing.splitlines())


def claude_desktop(scope: str, remove: bool, cwd: Path, url: str | None) -> Change:
    path = claude_desktop_config()
    entry = _stdio()  # its config file takes a command to start, never an address
    text = _json_entry(path, "mcpServers", None if remove else entry)
    if text is not None and _claude_is_open():
        # The app keeps its own settings in this file and writes it back as it runs: an edit made now would be lost, or lose one of its.
        raise RuntimeError("Claude is open, and it rewrites this file while it runs. Quit Claude, then run this again.")
    if remove:
        return Change(f"claude-desktop  removed from {path}" if text else f"claude-desktop  was not in {path}", path, text)
    return Change(f"claude-desktop  {path}. Open Claude again.", path, text, entry=_shown("mcpServers", entry))


PLAN: dict[str, Callable[[str, bool, Path, str | None], Change]] = {"claude-code": claude_code, "codex": codex, "claude-desktop": claude_desktop}


def detected() -> dict[str, str]:
    """Which agents look installed here, and the evidence."""
    found: dict[str, str] = {}
    if cli := shutil.which("claude"):
        found["claude-code"] = cli
    elif (Path.home() / ".claude.json").exists():
        found["claude-code"] = "~/.claude.json"
    if cli := shutil.which("codex"):
        found["codex"] = cli
    elif (Path.home() / ".codex").is_dir():
        found["codex"] = "~/.codex"
    if claude_desktop_config().exists():
        found["claude-desktop"] = "its config file"
    return found


def connect(
    clients: list[str], *, url: str | None = DEFAULT_URL, scope: str = "", remove: bool = False, dry: bool = False, cwd: Path | None = None, say: Callable[[str], None] = print,
) -> int:
    """Point each named agent at Catchbunny (at `url`, or with url None at a command that starts one), or take
    it out again. With no names, every agent of AUTO found on this machine. Returns a process exit code."""
    if not clients:
        found = detected()
        clients = [name for name in AUTO if name in found]
        if not clients:
            say("No Claude Code or Codex was found on this machine. Name the agent to connect, for example: catchbunny connect claude-code")
            for name in CLIENTS:
                say(f"  {name:<15} {'found (' + found[name] + ')' if name in found else 'not found on this machine'}")
            return 2
    code = 0
    for name in clients:
        try:
            change = PLAN[name](scope or ("" if name == "claude-code" else "user"), remove, cwd or Path.cwd(), url)
            say(f"{name}: {change.describe()}" if dry else change.apply())
        except (OSError, ValueError, RuntimeError, tomllib.TOMLDecodeError) as exc:
            say(f"{name}: {exc}")
            code = 1
    return code
