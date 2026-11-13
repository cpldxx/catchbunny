from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from catchbunny.config import Settings
from catchbunny.domain import Credential, Environment, Persona, Project
from catchbunny.engine.orchestrator import App

BENCH = Path(__file__).parent.parent / "bench"
sys.path.insert(0, str(BENCH))


def _serve(bugs: str, script: Path = BENCH / "buggy_shop.py") -> Iterator[str]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    args = ["--bugs", bugs] if bugs else []
    proc = subprocess.Popen([sys.executable, str(script), "--port", str(port), *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(url, timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError(f"{script.name} did not start")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture(scope="session")
def buggy_shop() -> Iterator[str]:
    yield from _serve("all")


@pytest.fixture(scope="session")
def fixed_shop() -> Iterator[str]:
    yield from _serve("none")


@pytest.fixture(scope="session")
def lab() -> Iterator[str]:
    """The app with what the shop lacks: uploads, drag and drop, iframes, closed shadow roots, per-account data."""
    yield from _serve("", Path(__file__).parent / "lab_app.py")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings()
    s.data_dir = tmp_path
    s.ollama_host = "http://127.0.0.1:9"  # nothing listens here: tests never talk to a developer's Ollama
    return s


@pytest.fixture
async def app(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[App]:
    # Tests never reach a model API, whatever the developer's environment holds.
    monkeypatch.setattr("catchbunny.brain.claude.llm_credentials_present", lambda: False)
    async with App(settings, in_memory=True) as core:
        yield core


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@asynccontextmanager
async def serving(asgi: Any, port: int = 0) -> AsyncIterator[str]:
    """Run an ASGI app on a real port for the length of a test; yields its address."""
    port = port or free_port()
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


def shop_project(url: str) -> Project:
    member = Persona(name="member", role="Signed-in customer", credential=Credential(username="demo", password="demo-pass"))
    return Project(name=url, environments=[Environment(base_url=url)], personas=[Persona(), member])
