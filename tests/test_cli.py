"""The commands a new user types first, run as they would run them: in a process of their own.
And the console's first screen, which offers the demo shop when there is nothing to test yet."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import httpx2
from conftest import free_port, serving

from catchbunny.api import DEMO_NAME, Serving, serve_app
from catchbunny.domain import Finding
from catchbunny.engine.orchestrator import App


def catchbunny(*args: str, data: Path, timeout: float = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "catchbunny", "--data-dir", str(data), *args], capture_output=True, text=True, timeout=timeout)


def test_the_help_lists_three_commands_and_the_older_ones_still_run(tmp_path: Path) -> None:
    listed = catchbunny("--help", data=tmp_path, timeout=60).stdout
    commands = [line.split()[0] for line in listed.split("commands:")[1].split("Start here")[0].splitlines()[2:] if line.startswith("    ") and not line.startswith("     ")]
    assert commands == ["start", "scan", "connect"], listed
    assert "catchbunny start" in listed
    bare = catchbunny(data=tmp_path, timeout=60)
    assert bare.returncode == 0 and "start" in bare.stdout, "no command is not an error: it is the help"
    for older in ("serve", "doctor", "export", "run", "mcp", "demo"):
        assert catchbunny(older, "--help", data=tmp_path, timeout=60).returncode == 0, f"`{older}` still works for scripts that use it"


def test_start_is_the_one_command_and_running_it_twice_finds_the_first(tmp_path: Path) -> None:
    port = free_port()
    env = {**os.environ, "CATCHBUNNY_DATA_DIR": str(tmp_path)}
    server = subprocess.Popen([sys.executable, "-m", "catchbunny", "start", "--port", str(port), "--no-open"], stderr=subprocess.PIPE, text=True, env=env)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(300):
            try:
                with urllib.request.urlopen(f"{base}/v1/meta", timeout=1) as reply:
                    assert b'"mode":"local"' in reply.read()
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("catchbunny start did not answer")
        with urllib.request.urlopen(f"{base}/app", timeout=5) as page:
            assert b"<div id=\"root\">" in page.read(), "the console is served"
        again = catchbunny("start", "--port", str(port), "--no-open", data=tmp_path, timeout=60)
        assert again.returncode == 0 and f"already running at {base}/app" in again.stderr, again.stderr
    finally:
        server.terminate()
        said = server.communicate(timeout=30)[1]
    assert f"{base}/app" in said and f"{base}/mcp" in said and "catchbunny connect" in said, said
    assert "Traceback" not in said, said


async def test_the_empty_console_scans_the_demo_shop_with_one_click(app: App) -> None:
    async with serving(serve_app(app, Serving(local_only=True))) as base, httpx2.AsyncClient(base_url=base, timeout=30) as http:
        started = (await http.post("/v1/demo")).json()
        assert started["run_id"].startswith("run_") and started["planted"] == 11
        projects = (await http.get("/v1/projects")).json()
        assert [p["name"] for p in projects] == [DEMO_NAME]
        run = await app.runs.wait(started["run_id"], timeout=240)
        findings = [Finding(**d) for d in await app.store.list("finding", parent=run.id)]
        assert run.status.value == "failed" and sum(f.status.value == "confirmed" for f in findings) >= 9, run.summary
        # Scanning it again, from the app's own page, finds the shop still there.
        again = await http.post(f"/v1/projects/{started['project_id']}/runs", json={"kind": "deep_scan", "brain": "heuristic", "testers": 1, "max_steps": 10})
        assert again.status_code == 202
        await app.runs.wait(again.json()["id"], timeout=120)
        assert len((await http.get("/v1/projects")).json()) == 1, "the demo shop is one app, however often it is scanned"
    shop = started["url"]
    try:
        httpx2.get(shop, timeout=2)
        raise AssertionError("the demo shop outlived the server it ran inside")
    except httpx2.TransportError:
        pass
