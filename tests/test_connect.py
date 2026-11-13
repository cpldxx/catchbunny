"""Reaching the MCP server from the outside: a scan that outlives one tool call, the
HTTP transport and its gate, the stdio command the installer writes, the installer
itself on throwaway config files, and two server processes sharing one data directory."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tomllib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from catchbunny import connect as installer
from catchbunny.domain import Run, RunSpec, RunStatus
from catchbunny.engine.orchestrator import App
from catchbunny.mcp_server import Gate, access_token, create_mcp, http_app

TOKEN = "test-token-not-a-secret"


async def call(client: Client, tool: str, **arguments: Any) -> Any:
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content
    data = result.structured_content
    return data["result"] if isinstance(data, dict) and set(data) == {"result"} else data


# ---------------------------------------------------------------- a scan is longer than a tool call


async def test_a_scan_that_outlives_the_tool_call_is_picked_up_by_run_status(app: App, buggy_shop: str) -> None:
    async with Client(create_mcp(app), raise_exceptions=True) as agent:
        first = await call(agent, "scan", url=buggy_shop, areas=["/products"], testers=4, max_steps=120, wait_seconds=0)
        assert first["status"] == "running" and first["run_id"].startswith("run_") and "run_status" in first["next"]
        assert first["max_steps"] == 120, "a running scan says how far it has got"

        result, calls = first, 0
        while result["status"] == "running":
            result, calls = await call(agent, "run_status", run_id=first["run_id"], wait_seconds=5), calls + 1
            assert calls < 60
        assert result["status"] == "failed" and any("Uncaught exception" in f["title"] for f in result["findings"])
        assert set(result) == {"status", "error", "run_id", "coverage", "steps", "stop_reason", "findings", "next"}, "the same shape scan gives when it does not have to hand over"
        assert await call(agent, "run_status", run_id=first["run_id"]) == result, "asking again after the end is harmless"

        missing = await agent.call_tool("run_status", {"run_id": "run_nope"})
        assert missing.is_error and "not found" in str(missing.content)


async def test_a_wait_is_capped_by_the_server_not_by_the_caller(app: App) -> None:
    async with Client(create_mcp(app, max_wait=0.2), raise_exceptions=True) as agent:
        started = asyncio.get_running_loop().time()
        scan = await call(agent, "scan", url="http://127.0.0.1:9", wait_seconds=3600, max_minutes=0.2)
        assert asyncio.get_running_loop().time() - started < 15 and scan["status"] in ("running", "blocked")


# ---------------------------------------------------------------- over HTTP


@asynccontextmanager
async def serving(asgi: Any) -> AsyncIterator[str]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(asgi, host="127.0.0.1", port=port, log_level="error"))
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.02)
        assert server.started
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}
ACCEPT = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


async def test_the_http_server_answers_only_with_the_token_as_a_header_or_in_the_url(app: App) -> None:
    async with serving(http_app(create_mcp(app, max_wait=5), token=TOKEN)) as base, httpx2.AsyncClient() as http:
        assert (await http.post(f"{base}/mcp", json=INIT, headers=ACCEPT)).status_code == 401
        assert (await http.post(f"{base}/mcp", json=INIT, headers={**ACCEPT, "authorization": "Bearer wrong"})).status_code == 401
        assert (await http.post(f"{base}/wrong/mcp", json=INIT, headers=ACCEPT)).status_code == 401
        hello = await http.get(base + "/")
        assert hello.status_code == 200 and TOKEN not in hello.text and "/mcp" in hello.text

        # A tunnel forwards requests under its own host name. The token is what lets them in.
        through_tunnel = await http.post(f"{base}/mcp", json=INIT, headers={**ACCEPT, "authorization": f"Bearer {TOKEN}", "host": "quiet-river.example"})
        assert through_tunnel.status_code == 200 and through_tunnel.json()["result"]["serverInfo"]["name"] == "catchbunny"

        # A client that sends a header (Claude Code, Codex, Claude's request headers)...
        async with httpx2.AsyncClient(headers={"authorization": f"Bearer {TOKEN}"}) as authed:
            async with Client(streamable_http_client(f"{base}/mcp", http_client=authed), raise_exceptions=True) as agent:
                assert len((await agent.list_tools()).tools) == 7
                assert (await call(agent, "scan", url="http://127.0.0.1:9", testers=1, max_steps=5))["status"] in ("blocked", "running")
        # ...and one that can only be given a URL.
        async with Client(f"{base}/{TOKEN}/mcp", raise_exceptions=True) as agent:
            assert {t.name for t in (await agent.list_tools()).tools} >= {"scan", "run_status", "verify_fix"}
            assert await call(agent, "list_findings", run_id="run_none") == []


async def test_without_a_token_only_this_machine_is_served(app: App) -> None:
    async with serving(http_app(create_mcp(app, max_wait=5))) as base, httpx2.AsyncClient() as http:
        assert (await http.post(f"{base}/mcp", json=INIT, headers=ACCEPT)).status_code == 200
        # What a tunnel sends, and what a web page reaching for localhost through the browser sends.
        assert (await http.post(f"{base}/mcp", json=INIT, headers={**ACCEPT, "host": "quiet-river.example"})).status_code >= 400
        assert (await http.post(f"{base}/mcp", json=INIT, headers={**ACCEPT, "origin": "https://evil.example"})).status_code >= 400


def test_the_token_is_made_once_and_kept_private(tmp_path: Path) -> None:
    token = access_token(tmp_path)
    assert len(token) >= 32 and access_token(tmp_path) == token
    assert (tmp_path / "mcp_token").stat().st_mode & 0o077 == 0
    assert isinstance(Gate(lambda *_: None, token), Gate)  # type: ignore[arg-type,return-value]


def test_the_cli_refuses_to_serve_the_network_without_a_token(tmp_path: Path) -> None:
    done = subprocess.run([sys.executable, "-m", "catchbunny", "--data-dir", str(tmp_path), "mcp", "--http", "--host", "0.0.0.0", "--port", "1"], capture_output=True, text=True, timeout=60, env={**os.environ, "CATCHBUNNY_MCP_TOKEN": ""})
    assert done.returncode == 2 and "without an access token" in done.stderr


# ---------------------------------------------------------------- the command an agent is given


async def test_the_command_the_installer_writes_starts_a_working_server(tmp_path: Path) -> None:
    command, *args = installer.launch()
    assert Path(command).is_absolute(), "an app started from the dock has no shell PATH"
    params = StdioServerParameters(command=command, args=args, env={**os.environ, "CATCHBUNNY_DATA_DIR": str(tmp_path)})
    async with Client(params, raise_exceptions=True) as agent:
        tools = {t.name for t in (await agent.list_tools()).tools}
        assert tools == {"scan", "run_status", "list_findings", "get_finding", "verify_fix", "keep_as_scenario", "run_scenarios"}
        assert await call(agent, "list_findings", run_id="run_none") == []
    assert (tmp_path / "catchbunny.db").exists(), "the server kept its records where it was told to"


def test_an_agent_started_server_keeps_its_records_in_one_place(tmp_path: Path) -> None:
    # No --data-dir and no CATCHBUNNY_DATA_DIR: whichever folder the agent is in, findings go to the home directory.
    env = {k: v for k, v in os.environ.items() if k != "CATCHBUNNY_DATA_DIR"} | {"HOME": str(tmp_path)}
    done = subprocess.run([sys.executable, "-m", "catchbunny", "doctor"], capture_output=True, text=True, timeout=120, env=env, cwd=tmp_path)
    assert f"data directory  {tmp_path / '.catchbunny'}" in done.stderr, done.stderr
    assert not (tmp_path / ".catchbunny").exists(), "a check leaves nothing behind"
    # A new home has no browser cache, which is what a fresh install looks like: the check fails and says how to fix it.
    found = "browser         ok" in done.stderr
    assert done.returncode == (0 if found else 1) and (found or "-m playwright install chromium" in done.stderr), done.stderr


# ---------------------------------------------------------------- the installer


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / "home").mkdir()
    (tmp_path / "project").mkdir()
    monkeypatch.setattr(installer, "_claude_is_open", lambda: False)
    return tmp_path


def run_connect(home: Path, *clients: str, **kwargs: Any) -> tuple[int, str]:
    said: list[str] = []
    code = installer.connect(list(clients), cwd=home / "project", say=said.append, **kwargs)
    return code, "\n".join(said)


def test_claude_code_gets_a_project_file_and_other_servers_are_left_alone(home: Path) -> None:
    file = home / "project" / ".mcp.json"
    file.write_text(json.dumps({"mcpServers": {"other": {"command": "other-server"}}, "unrelated": True}))
    code, said = run_connect(home, "claude-code")
    doc = json.loads(file.read_text())
    assert code == 0 and "approve the server" in said
    assert doc["mcpServers"]["catchbunny"] == {"command": installer.launch()[0], "args": ["mcp"]}
    assert doc["mcpServers"]["other"] == {"command": "other-server"} and doc["unrelated"] is True
    assert json.loads((home / "project" / ".mcp.json.bak").read_text())["mcpServers"] == {"other": {"command": "other-server"}}

    before = file.read_text()
    assert run_connect(home, "claude-code")[0] == 0 and file.read_text() == before, "connecting twice changes nothing"
    assert run_connect(home, "claude-code", remove=True)[0] == 0
    assert json.loads(file.read_text())["mcpServers"] == {"other": {"command": "other-server"}}


def test_user_scope_for_claude_code_goes_through_its_own_cli_or_says_how(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)
    code, said = run_connect(home, "claude-code", scope="user")
    assert code == 1 and "claude mcp add-json catchbunny" in said and "--scope project" in said


CODEX = """# my settings
model = "some-model"

[mcp_servers.other]
command = "other"  # keep me

[mcp_servers.other.env]
KEY = "value"

[mcp_servers.catchbunny]
command = "/old/path/catchbunny"
args = ["mcp"]

[mcp_servers.catchbunny.env]
STALE = "1"

[projects."/somewhere"]
trust_level = "trusted"
"""


def test_codex_gets_one_table_and_the_rest_of_its_file_is_untouched(home: Path) -> None:
    file = home / "home" / ".codex" / "config.toml"
    file.parent.mkdir()
    file.write_text(CODEX)
    code, said = run_connect(home, "codex")
    text = file.read_text()
    doc = tomllib.loads(text)
    ours = doc["mcp_servers"]["catchbunny"]
    assert code == 0 and "Restart Codex" in said
    assert ours == {"command": installer.launch()[0], "args": ["mcp"], "startup_timeout_sec": 30, "tool_timeout_sec": 120}, "the old table and its sub-table are replaced, not added to"
    assert doc["mcp_servers"]["other"] == {"command": "other", "env": {"KEY": "value"}} and doc["projects"]["/somewhere"]["trust_level"] == "trusted"
    assert "# my settings" in text and "# keep me" in text, "comments survive: the file is edited as text"
    assert (file.parent / "config.toml.bak").read_text() == CODEX

    assert run_connect(home, "codex")[0] == 0 and file.read_text() == text
    assert run_connect(home, "codex", remove=True)[0] == 0
    assert "catchbunny" not in tomllib.loads(file.read_text())["mcp_servers"] and "other" in file.read_text()


def test_a_file_that_is_already_broken_is_not_touched(home: Path) -> None:
    file = home / "home" / ".codex" / "config.toml"
    file.parent.mkdir()
    file.write_text("[mcp_servers.other\ncommand = ")
    code, said = run_connect(home, "codex")
    assert code == 1 and "codex:" in said and file.read_text() == "[mcp_servers.other\ncommand = "


def test_a_preview_writes_nothing_and_shows_only_our_own_entry(home: Path) -> None:
    file = home / "home" / ".codex" / "config.toml"
    file.parent.mkdir()
    file.write_text(CODEX)
    code, said = run_connect(home, "claude-code", "codex", "claude-desktop", dry=True)
    assert code == 0 and file.read_text() == CODEX and not (home / "project" / ".mcp.json").exists()
    assert "[mcp_servers.catchbunny]" in said and "some-model" not in said and "trust_level" not in said, "the rest of the file is the user's"


def test_claude_desktop_is_left_alone_while_it_is_open(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer, "_claude_is_open", lambda: True)
    code, said = run_connect(home, "claude-desktop")
    assert code == 1 and "Quit Claude" in said and not installer.claude_desktop_config().exists()
    monkeypatch.setattr(installer, "_claude_is_open", lambda: False)
    assert run_connect(home, "claude-desktop")[0] == 0
    assert json.loads(installer.claude_desktop_config().read_text())["mcpServers"]["catchbunny"]["args"] == ["mcp"]


def test_with_no_agent_named_it_says_what_it_found(home: Path) -> None:
    (home / "home" / ".codex").mkdir()
    code, said = run_connect(home)
    assert code == 2 and "codex           found" in said and "catchbunny mcp --http" in said


# ---------------------------------------------------------------- several servers, one data directory


async def test_a_starting_server_leaves_another_live_servers_run_alone(app: App) -> None:
    live = Run(project_id="prj_x", spec=RunSpec(), status=RunStatus.RUNNING, pid=os.getppid())  # some other process that is alive
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    dead = Run(project_id="prj_x", spec=RunSpec(), status=RunStatus.RUNNING, pid=gone.pid)
    for run in (live, dead):
        await app.store.put_now("run", run.id, run, parent=run.project_id, created=run.created_at)

    assert await app.runs.recover() == 1
    await app.store.flush()
    assert (await app.store.get("run", live.id))["status"] == "running"  # type: ignore[index]
    closed = await app.store.get("run", dead.id)
    assert closed["status"] == "error" and "Interrupted" in closed["error"]  # type: ignore[index]

    # Asked about the other server's run, this one waits its turn instead of answering at once or forever.
    started = asyncio.get_running_loop().time()
    seen = await app.runs.wait(live.id, timeout=0.6)
    assert seen.status is RunStatus.RUNNING and 0.5 <= asyncio.get_running_loop().time() - started < 5
    with pytest.raises(KeyError):
        await app.runs.wait("run_nope", timeout=0.1)
