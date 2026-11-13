"""What a tester can reach and do: other browser engines, uploads, drag and drop,
iframes, closed shadow roots, and an app's own reset between replays."""

from __future__ import annotations

import httpx
import pytest
from conftest import shop_project

from catchbunny.browser import samples
from catchbunny.browser.session import EngineUnavailable, Session, profile
from catchbunny.domain import Action, Credential, Environment, Finding, Persona, Project, RunKind, RunSpec, RunStatus, Signal, signal_signature
from catchbunny.engine.orchestrator import App
from catchbunny.engine.scenarios import finding_to_scenario, playwright_test, scenario_from_yaml, scenario_to_yaml
from catchbunny.engine.triage import describe


async def scan(app: App, project: Project, **spec: object) -> tuple[object, list[Finding]]:
    await app.store.put_now("project", project.id, project)
    defaults = {"kind": RunKind.DEEP_SCAN, "brain": "heuristic", "testers": 4, "max_steps": 300, "max_minutes": 3}
    run = await app.runs.wait((await app.runs.start(project, RunSpec(**{**defaults, **spec}))).id)  # type: ignore[arg-type]
    assert run.error == "", run.error
    return run, [Finding(**d) for d in await app.store.list("finding", parent=run.id)]


def confirmed(findings: list[Finding], needle: str) -> Finding:
    hit = next((f for f in findings if needle in f.title and f.status.value == "confirmed"), None)
    assert hit is not None, [(f.title, f.status.value) for f in findings]
    return hit


async def engine_or_skip(app: App, engine: str) -> None:
    try:
        async with app.pool.context(browser=engine):
            pass
    except EngineUnavailable as exc:
        pytest.skip(str(exc))


# ---------------------------------------------------------------- engines


@pytest.mark.parametrize("engine", ["webkit", "firefox"])
async def test_a_scan_in_another_engine_finds_proves_and_replays_there(app: App, buggy_shop: str, engine: str) -> None:
    await engine_or_skip(app, engine)
    project = shop_project(buggy_shop)
    run, findings = await scan(app, project, browser=engine, areas=["/products"], personas=["visitor"], max_steps=150)
    crash = next(f for f in findings if f.kind == "page_error")
    assert crash.status.value == "confirmed" and crash.browser == engine and crash.verification.reproduced == 2
    assert run.summary["browser"] == engine  # type: ignore[attr-defined]
    assert crash.evidence["video"] and crash.evidence["trace"], "evidence is recorded in every engine"
    assert (await app.runs.rerun_finding(project, crash, 1))["still_present"], "a finding is replayed in the engine that found it"
    kept = finding_to_scenario(crash)
    assert kept.browser == engine and f"browser: {engine}" in scenario_to_yaml(kept) and scenario_from_yaml(scenario_to_yaml(kept)).browser == engine
    assert f"pytest --browser {engine}" in playwright_test(crash, buggy_shop)


async def test_an_engine_that_is_not_there_blocks_the_run_and_says_how_to_get_it(app: App, fixed_shop: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from playwright.async_api import Error

    async def absent(**_: object) -> None:
        raise Error("BrowserType.launch: Executable doesn't exist at /nowhere/firefox")

    monkeypatch.setattr(app.pool._pw.firefox, "launch", absent)  # noqa: SLF001
    project = shop_project(fixed_shop)
    await app.store.put_now("project", project.id, project)
    run = await app.runs.wait((await app.runs.start(project, RunSpec(brain="heuristic", browser="firefox", max_steps=20))).id)
    assert run.status is RunStatus.BLOCKED and "catchbunny doctor --browser firefox --install-browser" in run.error


def test_firefox_gets_the_viewport_without_the_mobile_mode_it_does_not_have() -> None:
    assert profile("iphone", "webkit")["is_mobile"] is True
    assert "is_mobile" not in profile("iphone", "firefox") and profile("iphone", "firefox")["viewport"]["width"] == 390


# ---------------------------------------------------------------- uploads, drags, frames, shadow roots


async def test_an_upload_hidden_behind_its_label_is_given_files_and_a_hostile_name_is_caught(app: App, lab: str) -> None:
    _, findings = await scan(app, Project(name="lab", environments=[Environment(base_url=lab)]), areas=["/upload"])
    crash = confirmed(findings, "POST /upload returns 500")
    upload = next(a for a in crash.repro if a.kind == "upload")
    assert upload.value == "odd.png", "the control accepts images, and the name with markup in it is what breaks the server"
    assert "<" in samples.payload(upload.value)["name"] and samples.payload("sample.png")["buffer"][:4] == b"\x89PNG"
    assert crash.repro[-1].describe() == 'Click button "Send receipt"'
    compile(playwright_test(crash, lab), "t.py", "exec")
    assert "set_input_files" in playwright_test(crash, lab)


async def test_a_card_is_dragged_onto_the_column_that_takes_drops(app: App, lab: str) -> None:
    _, findings = await scan(app, Project(name="lab", environments=[Environment(base_url=lab)]), areas=["/board"])
    crash = confirmed(findings, "/move returns 500")
    drag = crash.repro[-1]
    assert drag.kind == "drag" and drag.to is not None and "Done" in drag.to.name, drag.describe()
    assert "drag_to" in playwright_test(crash, lab)


async def test_the_apps_own_frame_is_searched_and_someone_elses_is_left_alone(app: App, lab: str) -> None:
    async with app.pool.context() as ctx:
        session = await Session.open(ctx, base_url=lab, device="desktop", settings=app.settings)
        await session.goto("/embed")
        obs = await session.observe()
        save = next(e for e in obs.elements if e.name == "Save settings")
        assert save.frame == "name=settings" and "(in frame name=settings)" in obs.render()
        assert not [e for e in obs.elements if "Elsewhere" in e.name], "a frame from another origin is not the app under test"
        assert "h2: Notifications" in obs.headings
    _, findings = await scan(app, Project(name="lab", environments=[Environment(base_url=lab)]), areas=["/embed"])
    crash = confirmed(findings, "POST /api/settings returns 500")
    assert crash.repro[-1].target.frame == "name=settings"  # type: ignore[union-attr]
    assert "frame_locator" in playwright_test(crash, lab)


async def test_a_closed_shadow_root_does_not_hide_its_controls(app: App, lab: str) -> None:
    _, findings = await scan(app, Project(name="lab", environments=[Environment(base_url=lab)]), areas=["/widget"])
    crash = next(f for f in findings if f.kind == "page_error")
    assert crash.status.value == "confirmed" and crash.repro[-1].describe() == 'Click button "Rate five stars"'


# ---------------------------------------------------------------- the app's own reset


async def test_with_a_reset_a_reproduction_keeps_the_step_that_caused_the_bug(app: App, lab: str) -> None:
    """A note with a quote in it breaks the notes page for that account from then on. Without a
    reset, every replay starts on the broken account, so "open the page" looks like the whole bug."""

    def project(reset: str) -> Project:
        member = Persona(name="member", role="Signed-in user", credential=Credential(username="demo", password="demo-pass"))
        return Project(name="lab", environments=[Environment(base_url=lab, reset_path=reset)], personas=[member])

    async with httpx.AsyncClient(base_url=lab) as http:
        await http.post("/api/test/reset")
        before = (await http.get("/api/test/state")).json()["resets"]
        run, findings = await scan(app, project("/api/test/reset"), areas=["/notes"], testers=2)
        broken = confirmed(findings, "/notes returns 500")
        assert [a.kind for a in broken.repro] == ["navigate", "fill", "click"], broken.repro_text()
        assert '"' in broken.repro[1].value, "the note that poisons the account is part of the reproduction"
        resets = (await http.get("/api/test/state")).json()["resets"] - before
        assert resets >= run.summary["verification_replays"] > 0  # type: ignore[attr-defined]

        _, findings = await scan(app, project(""), areas=["/notes"], testers=2)  # the same hunt with no reset
        assert [a.kind for a in confirmed(findings, "/notes returns 500").repro] == ["navigate"], "without one, the cause is lost"
        await http.post("/api/test/reset")


async def test_a_reset_that_fails_is_said_once_and_does_not_stop_the_run(app: App, lab: str) -> None:
    project = Project(name="lab", environments=[Environment(base_url=lab, reset_path="/api/test/no-such-reset")])
    run, _ = await scan(app, project, areas=["/widget"])
    warnings = [e["data"]["message"] for e in await app.store.events_after(run.id, 0) if e["type"] == "run.warning"]  # type: ignore[attr-defined]
    assert len(warnings) == 1 and "did not work (it answered 404)" in warnings[0]


# ---------------------------------------------------------------- judgement


def test_a_dead_primary_action_matters_more_than_a_dead_decoration() -> None:
    def dead(label: str) -> Signal:
        return Signal(kind="dead_click", message="x", url="http://app/cart", detail={"label": label, "element": label})

    assert describe(dead('button "Apply coupon"'))[0].value == "P2"
    assert describe(dead('button "Place order"'))[0].value == "P2"
    assert describe(dead('button "Like"'))[0].value == "P3"


def test_one_defect_a_model_words_two_ways_is_one_finding() -> None:
    check = {"kind": "text_absent", "value": "Cart (2)", "role": "", "name": ""}
    a = signal_signature("agent_report", "Cart badge is stale", {"check": check}, "http://app/cart")
    b = signal_signature("agent_report", "Nav count does not drop after removal", {"check": check}, "http://app/cart")
    other = signal_signature("agent_report", "Cart badge is stale", {"check": {**check, "value": "Total: $-12.00"}}, "http://app/cart")
    assert a == b != other
    assert signal_signature("agent_report", "No check", {}, "") != signal_signature("agent_report", "Another", {}, "")


def test_an_action_says_what_it_did() -> None:
    from catchbunny.domain import ElementSig

    card, column = ElementSig(role="draggable", name="Write the report"), ElementSig(role="dropzone", name="Done")
    assert Action(kind="drag", target=card, to=column).describe() == 'Drag draggable "Write the report" onto dropzone "Done"'
    assert Action(kind="upload", target=ElementSig(role="file", name="Choose a receipt"), value="sample.png").describe() == 'Upload sample.png to file "Choose a receipt"'
