"""Whole-system behaviour against the demo shop (no model, no network)."""

from __future__ import annotations

import asyncio
import json

import httpx
from buggy_shop import BUGS
from conftest import shop_project
from run_bench import match

from catchbunny.api import create_app, WEB_DIST
from catchbunny.brain.base import Intent
from catchbunny.brain.heuristic import ScriptedBrain
from catchbunny.browser.observe import Observation
from catchbunny.browser.session import Session
from catchbunny.budget import Budget
from catchbunny.domain import Action, Check, ElementSig, Finding, Mission, Policy, RunKind, RunSpec, RunStatus, Scenario, Verdict
from catchbunny.engine.orchestrator import App
from catchbunny.engine.productmap import ProductMap
from catchbunny.engine.scenarios import ScenarioRunner, finding_to_scenario

DETECTABLE = {b for b, meta in BUGS.items() if not meta["semantic"]}


async def scan(app: App, url: str, **spec: object) -> tuple[object, list[Finding]]:
    project = shop_project(url)
    await app.store.put_now("project", project.id, project)
    defaults = {"kind": RunKind.DEEP_SCAN, "brain": "heuristic", "testers": 8, "max_steps": 2000, "max_minutes": 4}
    run = await app.runs.wait((await app.runs.start(project, RunSpec(**{**defaults, **spec}))).id)  # type: ignore[arg-type]
    assert run.error == "", run.error
    return run, [Finding(**d) for d in await app.store.list("finding", parent=run.id)]


async def test_swarm_finds_every_detectable_bug_then_regressions_lock_them_in(app: App, buggy_shop: str, fixed_shop: str) -> None:
    run, findings = await scan(app, buggy_shop, devices=["desktop", "iphone"])
    confirmed = [f for f in findings if f.status.value == "confirmed"]
    found = {match(f) for f in confirmed}
    assert found == DETECTABLE, f"missed {DETECTABLE - found}, unexpected {[f.title for f in confirmed if match(f) is None]}"
    assert run.status is RunStatus.FAILED and run.summary["blocking_findings"], "a confirmed P1 fails the gate"  # type: ignore[attr-defined]
    summary = run.summary["findings"]  # type: ignore[attr-defined]
    assert summary["repro_steps_after"] < summary["repro_steps_before"]
    assert all(f.verification.reproduced == f.verification.runs == 2 and f.check is not None for f in confirmed)
    order = next(f for f in confirmed if match(f) == "checkout_500")
    assert order.severity.value == "P1" and order.repro[-1].describe() == 'Click button "Place order"'
    logins = [e for e in await app.store.events_after(run.id, 0) if e["type"] == "login"]  # type: ignore[attr-defined]
    assert len(logins) == 1 and logins[0]["data"]["ok"], "many member testers, exactly one sign-in"

    async def regress(url: str) -> dict:
        project = shop_project(url)
        await app.store.put_now("project", project.id, project)
        for f in confirmed:
            sc = finding_to_scenario(f)
            await app.store.put_now("scenario", sc.id, sc, parent=project.id)
        result = await app.runs.wait((await app.runs.start(project, RunSpec(kind=RunKind.SCENARIOS, brain="heuristic", testers=8))).id)
        assert result.error == "", result.error
        return {"status": result.status, **result.summary}

    still_buggy, now_fixed = await regress(buggy_shop), await regress(fixed_shop)
    not_failing = [(s["name"], s["verdict"], s["reason"], [st["mode"] for st in s["steps"]], s["signals"]) for s in still_buggy["scenarios"] if s["verdict"] != "failed"]
    assert still_buggy["status"] is RunStatus.FAILED and not not_failing, not_failing
    assert still_buggy["failed"] == len(confirmed)
    not_passing = [(s["name"], s["verdict"], s["reason"], s["steps"]) for s in now_fixed["scenarios"] if s["verdict"] != "passed"]
    assert not not_passing, not_passing
    assert now_fixed["status"] is RunStatus.PASSED, now_fixed["not_run"]
    assert now_fixed["budget"]["llm"]["requests"] == 0 and now_fixed["steps_replayed_without_model"] > 0


async def test_fixed_build_with_traps_yields_no_findings(app: App, fixed_shop: str) -> None:
    run, findings = await scan(app, fixed_shop, devices=["desktop", "iphone"])
    assert [f.title for f in findings if f.status.value in ("confirmed", "flaky")] == []
    assert run.status is RunStatus.PASSED and run.summary["coverage"]["screens"] >= 12  # type: ignore[attr-defined]


async def test_discovery_drafts_scenarios_that_pass_on_replay(app: App, fixed_shop: str) -> None:
    run, _ = await scan(app, fixed_shop, kind=RunKind.DISCOVERY, testers=4, max_steps=400)
    drafted = run.summary["scenarios_drafted"]  # type: ignore[attr-defined]
    assert drafted, "form submissions observed during discovery become scenarios"
    scenarios = [Scenario(**d) for d in await app.store.list("scenario", parent=run.project_id)]  # type: ignore[attr-defined]
    assert all(s.learned and s.checks and "draft" in s.tags for s in scenarios)
    project = shop_project(fixed_shop)
    project.id = run.project_id  # type: ignore[attr-defined]
    rerun = await app.runs.wait((await app.runs.start(project, RunSpec(kind=RunKind.SCENARIOS, brain="heuristic", testers=4))).id)
    assert rerun.status is RunStatus.PASSED, [(s["name"], s["verdict"], s["reason"]) for s in rerun.summary["scenarios"]]


async def test_cancel_stops_a_running_swarm(app: App, buggy_shop: str) -> None:
    project = shop_project(buggy_shop)
    run = await app.runs.start(project, RunSpec(brain="heuristic", testers=4, max_steps=5000, max_minutes=5))
    await asyncio.sleep(1.5)
    assert await app.runs.cancel(run.id)
    done = await asyncio.wait_for(app.runs.wait(run.id), timeout=10)
    assert done.status is RunStatus.CANCELED
    assert app.pool.active == 0, "every browser context is released on cancellation"
    events = await app.store.events_after(run.id, 0)
    assert events[-1]["type"] == "run.finished" and any(e["type"] == "step" for e in events)


async def test_a_model_run_teaches_steps_that_later_replay_without_a_model(app: App, fixed_shop: str) -> None:
    """Scenario ladder: no learning -> the brain performs it; next run -> pure replay."""
    scenario = Scenario(
        name="Visitor adds a mug to the cart",
        start="/products/1",
        steps=["Add the product to the cart", "Open checkout"],
        checks=[Check(kind="url_contains", value="/checkout"), Check(kind="text_visible", value="Checkout")],
    )

    def target(obs: Observation, name: str) -> ElementSig:
        return ElementSig(ref=next(e.ref for e in obs.elements if e.name == name))

    class Performer(ScriptedBrain):
        """Plays the model: clicks by name on whatever page it is shown."""

        async def decide(self, obs: Observation, feedback: object) -> list[Intent]:
            if "/products/1" in obs.url:
                return [Intent(kind="act", action=Action(kind="click", target=target(obs, "Add to cart")))]
            if obs.url.endswith("/cart"):
                return [
                    Intent(kind="check", check=Check(kind="text_visible", value="Your cart")),
                    Intent(kind="step_done", step_index=0),
                    Intent(kind="act", action=Action(kind="click", target=target(obs, "Checkout"))),
                ]
            return [Intent(kind="step_done", step_index=1), Intent(kind="finish", verdict=Verdict.PASSED, summary="done")]

    async def run(learned: dict, brain: bool) -> object:
        mission = Mission(kind="scenario", title=scenario.name)
        async with app.pool.context() as ctx:
            session = await Session.open(ctx, base_url=fixed_shop, device="desktop", settings=app.settings)
            runner = ScenarioRunner(
                session=session, scenario=scenario, mission=mission, learned=learned, pmap=ProductMap(fixed_shop),
                budget=Budget(max_steps=50, max_seconds=60), policy=Policy.SAFE_WRITES, emit=lambda *_: None,
                brain_factory=(lambda *_: Performer([])) if brain else None,
            )  # fmt: skip
            return await runner.run()

    blocked = await run({}, brain=False)
    assert blocked.verdict is Verdict.BLOCKED and "no model" in blocked.reason  # type: ignore[attr-defined]

    first = await run({}, brain=True)
    assert first.verdict is Verdict.PASSED and [s.mode for s in first.steps] == ["model", "model"]  # type: ignore[attr-defined]
    assert len(first.learned) == 2 and first.learned[0].checks[0].value == "Your cart"  # type: ignore[attr-defined]

    second = await run({ls.step_hash: ls for ls in first.learned}, brain=False)  # type: ignore[attr-defined]
    assert second.verdict is Verdict.PASSED and [s.mode for s in second.steps] == ["learned", "learned"]  # type: ignore[attr-defined]


async def test_http_api_end_to_end(app: App, buggy_shop: str) -> None:
    api = create_app(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://api") as http:
        assert (await http.get("/healthz")).json()["status"] == "ok"
        missing = await http.get("/v1/runs/run_nope")
        assert missing.status_code == 404 and missing.json() == {"status": "error", "code": "NOT_FOUND", "message": "run run_nope not found", "retryable": False}

        body = {"name": "shop", "base_url": buggy_shop, "personas": [{"name": "visitor"}, {"name": "member", "credential": {"username": "demo", "password": "demo-pass"}}]}
        project = (await http.post("/v1/projects", json=body)).json()
        assert "demo-pass" not in str(project) and project["personas"][1]["has_credential"] is True, "secrets never leave the server"
        pid = project["id"]

        spec = {"kind": "deep_scan", "brain": "heuristic", "testers": 4, "max_steps": 150, "areas": ["/products"], "personas": ["visitor"]}
        started = await http.post(f"/v1/projects/{pid}/runs", json=spec, headers={"Idempotency-Key": "abc"})
        retried = await http.post(f"/v1/projects/{pid}/runs", json=spec, headers={"Idempotency-Key": "abc"})
        assert started.status_code == 202 and retried.json()["id"] == started.json()["id"], "a retried POST does not start a second run"
        rid = started.json()["id"]

        stream = (await http.get(f"/v1/runs/{rid}/events")).text
        events = [line.split(": ", 1)[1] for line in stream.splitlines() if line.startswith("event: ")]
        assert events[0] == "run.queued" and events[-1] == "run.finished" and "step" in events and "finding" in events
        stamps = [json.loads(line[6:])["ts"] for line in stream.splitlines() if line.startswith("data: ")]
        assert stamps == sorted(stamps) and stamps[-1] - stamps[0] < 120, "every event carries its time, so a client can redraw a finished run on its real timeline"
        catalog = (await http.get("/v1/models")).json()
        assert catalog["auto"] == "heuristic" and [p["id"] for p in catalog["providers"]] == ["heuristic", "claude", "ollama"]
        assert not catalog["providers"][1]["available"] and "ANTHROPIC_API_KEY" in catalog["providers"][1]["reason"]
        assert (await http.get("/v1/nope")).status_code == 404, "an API typo is never answered with the web console"
        if WEB_DIST.is_dir():  # the site is built: pages are served, and a missing file is not answered with a page
            assert "text/html" in (await http.get("/docs/quick-start")).headers["content-type"], "the site's own docs live at /docs"
            assert (await http.get("/assets/from-an-older-build.js")).status_code == 404
            assert (await http.get("/v1/docs")).status_code == 200, "the API reference moved under /v1"
        last_id = max(int(line.split(": ")[1]) for line in stream.splitlines() if line.startswith("id: "))
        resumed = (await http.get(f"/v1/runs/{rid}/events", headers={"Last-Event-ID": str(last_id - 1)})).text
        assert resumed.count("event: ") == 1, "resume returns only what the client has not seen"

        run = (await http.get(f"/v1/runs/{rid}")).json()
        assert run["status"] in ("failed", "passed") and run["summary"]["coverage"]["screens"] > 0
        findings = (await http.get(f"/v1/runs/{rid}/findings")).json()
        crash = next(f for f in findings if f["kind"] == "page_error")
        assert crash["status"] == "confirmed"
        fid = crash["id"]
        ev = crash["evidence"]  # recorded on a final replay of the minimal reproduction
        assert ev["timeline"][-1]["action"] == 'Click button "Show reviews"' and ev["fired_at_ms"] <= ev["duration_ms"]
        assert any(n["url"].endswith("/reviews") and n["status"] == 200 for n in ev["network"])
        assert any(c["level"] == "exception" and "map" in c["text"] for c in ev["console"])
        video = await http.get(f"/v1/artifacts/{ev['video']}")
        assert video.status_code == 200 and video.headers["content-type"] == "video/webm" and video.content[:4] == b"\x1aE\xdf\xa3"
        assert (await http.get(f"/v1/artifacts/{ev['trace']}")).content[:2] == b"PK", "a Playwright trace archive"
        assert (await http.get("/v1/artifacts/.hidden")).status_code == 404
        assert "Show reviews" in " ".join((await http.get(f"/v1/findings/{fid}")).json()["repro_text"])
        assert "no_signal" in (await http.get(f"/v1/findings/{fid}/export?format=scenario")).text
        compile((await http.get(f"/v1/findings/{fid}/export?format=playwright")).text, "t.py", "exec")
        assert "Steps to reproduce" in (await http.get(f"/v1/findings/{fid}/export?format=prompt")).text
        assert (await http.post(f"/v1/findings/{fid}/rerun?runs=2")).json() == {"finding_id": fid, "runs": 2, "reproduced": 2, "still_present": True}

        kept = (await http.post(f"/v1/findings/{fid}/scenario")).json()
        listed = (await http.get(f"/v1/projects/{pid}/scenarios")).json()
        assert [s["id"] for s in listed] == [kept["id"]] and kept["source_finding"] == fid
        yaml_text = (await http.get(f"/v1/scenarios/{kept['id']}?format=yaml")).text
        again = await http.post(f"/v1/projects/{pid}/scenarios", json={"yaml": yaml_text})
        assert again.status_code == 201 and again.json()["steps"] == kept["steps"]
        assert (await http.post(f"/v1/projects/{pid}/scenarios", json={"yaml": "not: a scenario"})).status_code == 422
        ranked = (await http.post(f"/v1/projects/{pid}/relevance", json={"changed_files": ["web/product/ReviewsList.tsx"]})).json()
        assert ranked and ranked[0]["selected"] and "reviews" in ranked[0]["matched"]

        missions = (await http.get(f"/v1/runs/{rid}/missions")).json()
        steps = (await http.get(f"/v1/missions/{missions[0]['id']}/steps")).json()
        assert steps[0]["action"]["kind"] == "navigate"
        states = (await http.get(f"/v1/projects/{pid}/map")).json()["states"]
        assert any(s["path"].startswith("/products/") for s in states)
        assert (await http.post(f"/v1/runs/{rid}/cancel")).status_code == 409


async def test_an_unreachable_app_blocks_the_run_instead_of_passing_it(app: App) -> None:
    project = shop_project("http://127.0.0.1:9")  # nothing listens here
    run = await app.runs.wait((await app.runs.start(project, RunSpec(brain="heuristic", testers=2, max_steps=20))).id)
    assert run.status is RunStatus.BLOCKED and "could not be opened" in run.error
    assert await app.store.list("finding", parent=run.id) == [], "an app that is down is not a bug report"


async def test_a_persona_that_cannot_sign_in_blocks_the_run_and_says_why(app: App, fixed_shop: str) -> None:
    project = shop_project(fixed_shop)
    project.personas[1].credential.password = "wrong"  # type: ignore[union-attr]
    run = await app.runs.wait((await app.runs.start(project, RunSpec(brain="heuristic", testers=4, max_steps=2000))).id)
    assert run.status is RunStatus.BLOCKED and "Sign-in as 'member' failed" in run.error
    blocked = run.summary["blocked_missions"]
    assert blocked and all("member" in b["mission"] for b in blocked), "only the member missions are blocked"
    assert run.summary["coverage"]["screens"] >= 10, "the visitor missions still ran"


async def test_runs_interrupted_by_a_crash_are_closed_out_on_restart(settings: object) -> None:
    from catchbunny.domain import Run

    async with App(settings) as first:
        orphan = Run(project_id="prj_x", spec=RunSpec(), status=RunStatus.RUNNING)
        await first.store.put_now("run", orphan.id, orphan, parent="prj_x")
    async with App(settings) as second:  # same data dir = a restart
        recovered = Run(**await second.store.get("run", orphan.id))  # type: ignore[arg-type]
        assert recovered.status is RunStatus.ERROR and "Interrupted" in recovered.error
        events = await second.store.events_after(orphan.id, 0)
        assert events and events[-1]["type"] == "run.finished", "a client still streaming the old run gets its ending"
