"""The MCP surface, driven by a real MCP client over an in-memory transport,
plus the vault that keeps test-account passwords out of the database."""

from __future__ import annotations

import json
from typing import Any

from mcp import Client

from catchbunny.engine.orchestrator import App
from catchbunny.mcp_server import create_mcp
from catchbunny.projects import ensure_project
from catchbunny.vault import PREFIX


async def call(client: Client, tool: str, **arguments: Any) -> Any:
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content
    data = result.structured_content
    return data["result"] if isinstance(data, dict) and set(data) == {"result"} else data


async def test_a_coding_agent_can_find_fix_and_lock_in_a_bug(app: App, buggy_shop: str, fixed_shop: str) -> None:
    async with Client(create_mcp(app), raise_exceptions=True) as agent:
        tools = {t.name: t for t in (await agent.list_tools()).tools}
        assert set(tools) == {"scan", "run_status", "list_findings", "get_finding", "verify_fix", "keep_as_scenario", "run_scenarios"}
        assert tools["get_finding"].annotations.read_only_hint and not tools["scan"].annotations.read_only_hint, "a client asks before the tools that act"
        assert "TEST account" in tools["scan"].description and set(tools["scan"].input_schema["required"]) == {"url"}

        # 1. "Did I break anything under /products?"
        scan = await call(agent, "scan", url=buggy_shop, areas=["/products"], testers=4, max_steps=200, wait_seconds=600)
        assert scan["status"] == "failed" and scan["coverage"]["screens"] > 0
        crash = next(f for f in scan["findings"] if "Uncaught exception" in f["title"])
        assert crash["status"] == "confirmed" and set(crash) == {"id", "severity", "status", "title", "url", "steps"}, "lists stay small"
        assert len(json.dumps(scan)) < 4000, "a scan result must not flood the agent's context"
        assert await call(agent, "list_findings", run_id=scan["run_id"]) == [*scan["findings"]]

        # 2. "What exactly is wrong?"
        detail = await call(agent, "get_finding", finding_id=crash["id"])
        assert detail["steps_to_reproduce"] == ["Open /products/3", 'Click button "Show reviews"']
        assert "map" in detail["evidence"]["message"] and detail["evidence"]["artifacts"]["video"].endswith(".webm")
        assert detail["task"].startswith("Fix this defect") and detail["reproduced"].startswith("2/2")

        # 3. "Is it fixed?" - not yet: the code has not changed.
        assert (await call(agent, "verify_fix", finding_id=crash["id"]))["still_present"] is True

        # 4. Lock it in as a regression file, then run that file against both builds.
        kept = await call(agent, "keep_as_scenario", finding_id=crash["id"])
        assert kept["suggested_path"].startswith("qa/") and "no_signal" in kept["yaml"]
        before = await call(agent, "run_scenarios", url=buggy_shop, scenarios_yaml=[kept["yaml"]], wait_seconds=600)
        after = await call(agent, "run_scenarios", url=fixed_shop, scenarios_yaml=[kept["yaml"]], wait_seconds=600)
        assert (before["status"], before["scenarios"][0]["verdict"]) == ("failed", "failed")
        assert (after["status"], after["scenarios"][0]["verdict"]) == ("passed", "passed")
        assert await app.store.list("scenario") == [], "scenario files are the source of truth; nothing is left behind"

        missing = await agent.call_tool("get_finding", {"finding_id": "fnd_nope"})
        assert missing.is_error and "not found" in str(missing.content), "an anticipated failure is a readable tool result, not a crash"
        broken = await agent.call_tool("run_scenarios", {"url": fixed_shop, "scenarios_yaml": ["just: text"]})
        assert broken.is_error and "not a valid scenario" in str(broken.content)


async def test_an_agent_is_told_plainly_when_the_app_is_not_running(app: App) -> None:
    async with Client(create_mcp(app), raise_exceptions=True) as agent:
        scan = await call(agent, "scan", url="http://127.0.0.1:9", testers=2, max_steps=10, wait_seconds=600)
        assert scan["status"] == "blocked" and "could not be opened" in scan["error"] and scan["findings"] == []


async def test_passwords_are_ciphertext_on_disk_and_usable_in_memory(app: App, fixed_shop: str) -> None:
    project = await ensure_project(app, fixed_shop, user="demo", password="demo-pass")
    raw = await app.store.get("project", project.id)
    stored = raw["personas"][-1]["credential"]["password"]  # type: ignore[index]
    assert "demo-pass" not in json.dumps(raw) and stored.startswith(PREFIX)
    again = app.load_project(raw)  # type: ignore[arg-type]
    assert again.personas[-1].credential.password == "demo-pass"  # type: ignore[union-attr]
    await app.save_project(again)  # saving twice must not double-encrypt
    assert app.load_project(await app.store.get("project", project.id)).personas[-1].credential.password == "demo-pass"  # type: ignore[arg-type,union-attr]
    # The same origin maps to the same project, and a signed-in run still works end to end.
    assert (await ensure_project(app, fixed_shop, user="demo", password="demo-pass")).id == project.id
    from catchbunny.domain import RunSpec, RunStatus

    run = await app.runs.wait((await app.runs.start(again, RunSpec(brain="heuristic", testers=4, max_steps=120, personas=["member"], areas=["/account"]))).id)
    assert run.status is RunStatus.PASSED, run.error
    logins = [e for e in await app.store.events_after(run.id, 0) if e["type"] == "login"]
    assert logins and logins[0]["data"]["ok"]
