"""The commands a new user types first, run as they would run them: in a process of their own."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def catchbunny(*args: str, data: Path, timeout: float = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "catchbunny", "--data-dir", str(data), *args], capture_output=True, text=True, timeout=timeout)


def test_try_shows_the_whole_product_in_one_command(tmp_path: Path) -> None:
    """`catchbunny try`: the demo shop, a scan of it, and the console on that scan, with nothing set up first."""
    done = catchbunny("try", "--no-open", "--exit", data=tmp_path)
    said = done.stdout + done.stderr
    assert done.returncode == 1, said[-2000:]  # the shop has planted bugs, so the run fails its gate
    assert "Traceback" not in said, "a planted crash is a 500 to the tester, not a stack trace in the user's terminal"
    assert said.count("   confirmed ") >= 9, said[-3000:]
    assert "/app/runs/run_" in said and "catchbunny scan http://localhost:3000" in said, "it says where to look, and what to do next"
    assert (tmp_path / "catchbunny.db").is_file(), "the run is kept, so `catchbunny serve` shows it later"


def test_demo_and_help_are_there_for_an_installed_copy(tmp_path: Path) -> None:
    listed = catchbunny("--help", data=tmp_path, timeout=60).stdout
    assert all(command in listed for command in ("try", "demo", "scan", "serve", "connect"))
