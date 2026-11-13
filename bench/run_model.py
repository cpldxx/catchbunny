"""Measure what a model-driven run adds on the benchmark shop.

    uv run python bench/run_model.py --brain claude                 # needs ANTHROPIC_API_KEY
    uv run python bench/run_model.py --brain ollama --model qwen3:8b

The shop has two bugs where nothing errors (a negative quantity makes a negative
total; the cart counter does not drop). Only a tester that understands the page
can find those, and it has to prove them with a check that replays. This runs
one deep scan with the model driving the goal-directed testers, says which of
the two were confirmed and what it cost, and records it in
web/src/landing/models.json, which is where the landing page reads its numbers.

It spends real money with Claude: at most --usd (2 dollars unless you say otherwise).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from buggy_shop import BUGS  # noqa: E402
from run_bench import match, project_for, start_shop  # noqa: E402

from catchbunny.config import Settings  # noqa: E402
from catchbunny.domain import Finding, RunKind, RunSpec  # noqa: E402
from catchbunny.engine.orchestrator import App  # noqa: E402

HERE = Path(__file__).parent
SITE = HERE.parent / "web" / "src" / "landing" / "models.json"
QUIET = sorted(b for b, meta in BUGS.items() if meta["semantic"])
GOAL = "Quantities, prices, totals and counters: whatever the cart and the navigation show must agree with what was done."


async def measure(brain: str, model: str, usd: float, testers: int, steps: int) -> dict[str, Any]:
    settings = Settings()
    settings.data_dir = Path(tempfile.mkdtemp(prefix="catchbunny-model-"))
    shop, url = await start_shop("all", 25)
    try:
        async with App(settings, in_memory=True) as app:
            project = project_for(url)
            await app.store.put_now("project", project.id, project)
            spec = RunSpec(kind=RunKind.DEEP_SCAN, brain=brain, model=model, goal=GOAL, testers=testers, max_steps=steps, max_minutes=20, max_usd=usd)  # type: ignore[arg-type]
            run = await app.runs.wait((await app.runs.start(project, spec)).id)
            if run.error and run.status.value in ("blocked", "error"):
                raise SystemExit(f"The run did not happen: {run.error}")
            findings = [Finding(**d) for d in await app.store.list("finding", parent=run.id)]
            confirmed = {match(f) for f in findings if f.status.value == "confirmed"}
            llm = run.summary["budget"]["llm"]
            return {
                "model": run.summary.get("model") or model or brain,
                "found": sorted(b for b in QUIET if b in confirmed),
                "loud_found": len([b for b in confirmed if b and b not in QUIET]),
                "unverified": [f.title for f in findings if f.status.value == "unverified"],
                "usd": round(llm["usd"], 4),
                "requests": llm["requests"],
                "steps": run.summary["budget"]["steps"],
            }
    finally:
        shop.terminate()


def record(brain: str, result: dict[str, Any]) -> None:
    """Put the measurement where the landing page reads it. Nothing is written for a run that did not finish."""
    if not SITE.parent.is_dir():
        return
    data = json.loads(SITE.read_text()) if SITE.exists() else {"ollama": None, "claude": None}
    if brain == "claude":
        data["claude"] = {"model": result["model"], "found": len(result["found"]), "bugs": result["found"], "usd": result["usd"], "requests": result["requests"]}
    else:
        before = data.get("ollama") or {}
        same = before.get("model") == result["model"]
        found = dict(before.get("found", {})) if same else {}
        for bug in QUIET:
            found[bug] = found.get(bug, 0) + (bug in result["found"])
        data["ollama"] = {"model": result["model"], "runs": (before.get("runs", 0) if same else 0) + 1, "found": found}
    SITE.write_text(json.dumps(data, indent=1) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--brain", required=True, choices=["claude", "ollama"])
    ap.add_argument("--model", default="", help="a Claude model id or an Ollama tag; the configured default when left out")
    ap.add_argument("--usd", type=float, default=2.0, help="the most to spend (Claude)")
    ap.add_argument("--testers", type=int, default=6)
    ap.add_argument("--steps", type=int, default=600)
    args = ap.parse_args()
    result = asyncio.run(measure(args.brain, args.model, args.usd, args.testers, args.steps))
    record(args.brain, result)
    print(json.dumps(result, indent=2))
    print(f"\n{len(result['found'])} of {len(QUIET)} bugs where nothing errors were confirmed: {', '.join(result['found']) or 'none'}.")
    print(f"{result['requests']} model requests, ${result['usd']}. Recorded in {SITE.relative_to(HERE.parent)}; rebuild the site to show it.")
