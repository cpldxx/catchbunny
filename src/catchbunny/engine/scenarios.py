"""Scenarios: saved, repeatable tests - and how a finding becomes one.

A scenario is plain-English steps plus outcomes. Running one is a ladder of
decreasing cost:

* **Learned**   - replay the actions a passing run recorded for the step, then
                  re-evaluate its typed checks on the live page. No model turn.
* **Healed**    - same, but a control had moved and was re-found by signature.
* **Model**     - no usable learning (new step, changed page): a model-driven
                  tester takes over from that step and finishes the scenario.

A learned step never passes on its history: the saved checks must pass against
the page as it is now. Outcomes are judged from typed checks first; plain
English outcomes go to an independent model review of the evidence.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import yaml

from ..brain.base import Brain
from ..brain.llm import LLMGateway, judge_outcomes
from ..browser import samples
from ..browser.session import Session
from ..budget import Budget
from ..domain import Action, CheckResult, ElementSig, Finding, LearnedStep, Mission, Policy, Scenario, Signal, Step, Verdict, step_hash, url_path
from .productmap import ProductMap
from .replay import replay
from .tester import Emit, Tester

# --------------------------------------------------------------------------- files


def scenario_to_yaml(s: Scenario) -> str:
    data = s.model_dump(mode="json", exclude={"id", "project_id", "created_at"}, exclude_defaults=True)
    data = {"name": s.name, **{k: v for k, v in data.items() if k != "name"}}
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110)


def scenario_from_yaml(text: str, project_id: str = "") -> Scenario:
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or "name" not in data:
        raise ValueError("a scenario file needs at least a `name`")
    return Scenario(project_id=project_id, **data)


# --------------------------------------------------------------------------- finding -> regression artefacts


def finding_to_scenario(f: Finding) -> Scenario:
    """A confirmed finding becomes a zero-model regression test: its minimal
    reproduction as learned steps, and the oracle that fails while the bug lives."""
    start = f.repro[0].value if f.repro and f.repro[0].kind == "navigate" else "/"
    body = f.repro[1:] if f.repro and f.repro[0].kind == "navigate" else f.repro
    steps = [a.describe() for a in body]
    return Scenario(
        project_id=f.project_id,
        name=f"Regression: {f.title}"[:120],
        persona=f.persona,
        device=f.device,
        browser=f.browser,
        start=start,
        steps=steps,
        checks=[f.check] if f.check else [],
        outcomes=[f.expected] if f.expected and not f.check else [],
        learned=[LearnedStep(step_hash=step_hash(text), actions=[a], source_run=f.run_id) for text, a in zip(steps, body, strict=True)],
        tags=["regression", f.severity.value],
        source_finding=f.id,
    )


def fix_prompt(f: Finding, base_url: str) -> str:
    """The finding, phrased as a task for a coding agent."""
    steps = "\n".join(f"{i}. {t}" for i, t in enumerate(f.repro_text(), 1))
    signal = f.evidence.get("signal", {})
    stack = signal.get("detail", {}).get("stack", "")
    return (
        f"Fix this defect found by Catchbunny in {base_url}.\n\n"
        f"**{f.severity.value} - {f.title}**\n"
        f"Status: {f.status.value} ({f.verification.reproduced}/{f.verification.runs} fresh-browser replays reproduced it)\n"
        f"Persona: {f.persona} | Device: {f.device} | Browser: {f.browser} | URL: {f.url}\n\n"
        f"Expected: {f.expected}\nActual: {f.actual}\n\n"
        f"Steps to reproduce:\n{steps}\n\n"
        f"Evidence: {signal.get('message', '')}\n" + (f"Stack:\n{stack}\n" if stack else "") + "\n"
        "Find the root cause, fix it, and add a regression test. "
        f"Then re-run the saved scenario to confirm: `catchbunny run --url {base_url} <scenario.yaml>`."
    )


def _py(text: str) -> str:
    return repr(text)


def _locator(sig: ElementSig) -> str:
    a = sig.attrs
    page = "page"
    if sig.frame:  # a control in one of the app's iframes: step into the frame first
        kind, _, value = sig.frame.partition("#")[0].partition("=")
        page = f"page.frame_locator({_py('iframe[name=' + chr(34) + value + chr(34) + ']' if kind == 'name' else 'iframe[src*=' + chr(34) + value.replace('#', '') + chr(34) + ']')})"
    if a.get("data-testid"):
        return f"{page}.get_by_test_id({_py(a['data-testid'])})"
    if a.get("id"):
        return f"{page}.locator({_py('#' + a['id'])})"
    if a.get("name") and sig.tag:
        return f"{page}.locator({_py(sig.tag + '[name=' + chr(34) + a['name'] + chr(34) + ']')})"
    if sig.tag == "a" and a.get("href"):
        return f"{page}.locator({_py('a[href=' + chr(34) + a['href'] + chr(34) + ']')}).first"
    if sig.name and sig.role in ("link", "button", "textbox", "searchbox", "spinbutton", "combobox", "checkbox", "radio", "tab"):
        return f"{page}.get_by_role({_py(sig.role)}, name={_py(sig.name)}, exact=True).first"
    return f"{page}.locator({_py(sig.css)})"


def playwright_test(f: Finding, base_url: str) -> str:
    """Export a finding as a plain pytest-playwright test (no Catchbunny dependency)."""
    name = re.sub(r"[^a-z0-9]+", "_", f.title.lower()).strip("_")[:60] or "finding"
    lines = [
        '"""Generated by Catchbunny from a confirmed finding. Fails while the bug exists."""',
        "from playwright.sync_api import Page",
        "",
        f"BASE_URL = {_py(base_url.rstrip('/'))}",
        "",
        "",
        f"def test_{name}(page: Page) -> None:",
        f"    # {f.severity.value}: {f.title}",
        *([f"    # Found in {f.browser}: run with `pytest --browser {f.browser}`"] if f.browser != "chromium" else []),
        f"    # Expected: {f.expected}",
        "    page_errors: list[str] = []",
        "    console_errors: list[str] = []",
        "    responses: list[tuple[str, str, int]] = []",
        '    page.on("pageerror", lambda e: page_errors.append(str(e)))',
        '    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)',
        '    page.on("response", lambda r: responses.append((r.request.method, r.url, r.status)))',
    ]
    for a in f.repro:
        loc = _locator(a.target) if a.target else ""
        match a.kind:
            case "navigate":
                lines.append(f"    page.goto(BASE_URL + {_py(a.value if a.value.startswith('/') else '/' + a.value)})")
            case "click":
                lines.append(f"    {loc}.click()")
            case "fill":
                value = "CREDENTIAL_" + a.secret.upper() if a.secret else _py(a.value)
                lines.append(f"    {loc}.fill({value})" + ("  # supply the persona's credential" if a.secret else ""))
            case "select":
                lines.append(f"    {loc}.select_option({_py(a.value)})")
            case "check":
                lines.append(f"    {loc}.check()")
            case "press":
                lines.append(f"    page.keyboard.press({_py(a.value or 'Enter')})")
            case "back":
                lines.append("    page.go_back()")
            case "hover":
                lines.append(f"    {loc}.hover()")
            case "upload":
                sample = samples.payload(a.value)
                size = len(sample["buffer"])
                body = f"bytes({size})" if size > 4096 else repr(sample["buffer"])
                lines.append(f"    {loc}.set_input_files({{'name': {_py(sample['name'])}, 'mimeType': {_py(sample['mimeType'])}, 'buffer': {body}}})")
            case "drag":
                lines.append(f"    {loc}.drag_to({_locator(a.to) if a.to else loc})")
            case _:
                lines.append(f"    page.wait_for_timeout({int(a.value or 300)})")
    lines.append('    page.wait_for_load_state("networkidle")')
    detail = f.evidence.get("signal", {}).get("detail", {})
    match f.kind:
        case "page_error":
            lines.append("    assert not page_errors, page_errors")
        case "console_error":
            lines.append("    assert not console_errors, console_errors")
        case "http_error":
            path = url_path(str(detail.get("url", "")))
            status = int(detail.get("status", 500) or 500)
            lines.append(f"    bad = [r for r in responses if r[2] == {status} and {_py(path.rsplit('/', 1)[0] or '/')} in r[1]]")
            lines.append("    assert not bad, bad")
        case "broken_image":
            lines.append('    assert page.evaluate("() => [...document.images].every(i => !i.complete || i.naturalWidth > 0)")')
        case "layout":
            lines.append('    assert page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 8")')
        case "a11y":
            css = str(detail.get("element", ""))
            lines.append(f"    el = page.locator({_py(css)})")
            lines.append('    assert el.get_attribute("aria-label") or el.evaluate("e => e.labels && e.labels.length > 0") or el.get_attribute("placeholder")')
        case _:
            c = f.check
            if c and c.kind == "text_visible":
                lines.append(f"    assert page.get_by_text({_py(c.value)}).first.is_visible()")
            elif c and c.kind == "text_absent":
                lines.append(f"    assert not page.get_by_text({_py(c.value)}).first.is_visible()")
            elif c and c.kind == "url_contains":
                lines.append(f"    assert {_py(c.value)} in page.url")
            else:
                lines.append("    # This oracle has no plain-Playwright equivalent; run the exported Catchbunny scenario instead.")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- runner


@dataclass(slots=True)
class StepReport:
    index: int
    text: str
    mode: str  # learned | healed | model | not_run
    passed: bool
    detail: str = ""


@dataclass(slots=True)
class ScenarioReport:
    scenario: Scenario
    mission: Mission
    verdict: Verdict = Verdict.INCONCLUSIVE
    reason: str = ""
    steps: list[StepReport] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    outcomes: list[dict[str, str]] = field(default_factory=list)
    trace: list[Step] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    learned: list[LearnedStep] = field(default_factory=list)
    model_turns_saved: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario.id,
            "name": self.scenario.name,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "steps": [{"index": s.index, "text": s.text, "mode": s.mode, "passed": s.passed, "detail": s.detail} for s in self.steps],
            "checks": [c.model_dump(mode="json") for c in self.checks],
            "outcomes": self.outcomes,
            "signals": [{"kind": s.kind, "message": s.message, "signature": s.signature} for s in self.signals],
            "learned_steps": sum(s.mode in ("learned", "healed") for s in self.steps),
            "model_steps": sum(s.mode == "model" for s in self.steps),
        }


BrainFactory = Callable[[Mission, Scenario, int], Brain]


class ScenarioRunner:
    def __init__(
        self,
        *,
        session: Session,
        scenario: Scenario,
        mission: Mission,
        learned: dict[str, LearnedStep],
        pmap: ProductMap,
        budget: Budget,
        policy: Policy,
        emit: Emit,
        brain_factory: BrainFactory | None = None,
        gateway: LLMGateway | None = None,
        run_id: str = "",
    ) -> None:
        self.session, self.scenario, self.mission = session, scenario, mission
        self.learned, self.pmap, self.budget, self.policy, self.emit = learned, pmap, budget, policy, emit
        self.brain_factory, self.gateway, self.run_id = brain_factory, gateway, run_id

    async def run(self) -> ScenarioReport:
        sc, rep = self.scenario, ScenarioReport(scenario=self.scenario, mission=self.mission)
        await self.session.act(Action(kind="navigate", value=sc.start), live=False)
        obs = await self.session.observe()
        rep.signals += self.session.drain(0)
        handover = len(sc.steps)
        for i, text in enumerate(sc.steps):
            entry = self.learned.get(step_hash(text))
            if entry is None:
                handover = i
                break
            out = await replay(self.session, entry.actions, before=obs)
            rep.signals += out.signals
            obs = out.final or obs
            ok = out.failed_at < 0
            detail = out.error
            if ok:
                for check in entry.checks:  # history proves nothing: re-check the live page
                    res = await self.session.run_check(check, obs, rep.signals)
                    rep.checks.append(res)
                    if not res.passed:
                        ok, detail = False, f"learned check failed: {check.describe()}"
                        break
            if not ok:
                handover = i  # replay could not complete this step: the model continues from here
                self.emit("scenario.step", {"scenario_id": sc.id, "index": i, "mode": "replay_failed", "detail": detail})
                break
            mode = "healed" if out.healed else "learned"
            rep.steps.append(StepReport(i, text, mode, True))
            rep.learned.append(entry)
            rep.model_turns_saved += 1
            self.emit("scenario.step", {"scenario_id": sc.id, "index": i, "mode": mode, "passed": True})

        if handover < len(sc.steps):
            if self.brain_factory is None:
                rep.verdict = Verdict.BLOCKED
                rep.reason = f"Step {handover + 1} has no usable learned actions and no model is configured to perform it."
                rep.steps += [StepReport(j, sc.steps[j], "not_run", False) for j in range(handover, len(sc.steps))]
                return rep
            tester = Tester(
                mission=self.mission,
                session=self.session,
                brain=self.brain_factory(self.mission, sc, handover),
                pmap=self.pmap,
                budget=self.budget,
                policy=self.policy,
                emit=self.emit,
                scenario=sc,
                run_id=self.run_id,
            )
            result = await tester.run(start=False)
            rep.trace, rep.signals = result.trace, rep.signals + result.signals()
            rep.checks += result.checks
            rep.learned += result.learned
            for j in range(handover, len(sc.steps)):
                passed = result.step_results.get(j)
                rep.steps.append(StepReport(j, sc.steps[j], "model" if passed is not None else "not_run", bool(passed)))
            if self.mission.verdict in (Verdict.FAILED, Verdict.BLOCKED) or not all(s.passed for s in rep.steps):
                rep.verdict = self.mission.verdict if self.mission.verdict in (Verdict.FAILED, Verdict.BLOCKED) else Verdict.FAILED
                rep.reason = self.mission.summary or "A step could not be completed."
                return rep

        await self._judge(rep)
        return rep

    async def _judge(self, rep: ScenarioReport) -> None:
        sc = self.scenario
        obs = await self.session.observe()
        rep.signals += self.session.drain(len(rep.trace))
        typed = [await self.session.run_check(c, obs, rep.signals) for c in sc.checks]
        rep.checks += typed
        failed = next((r for r in typed if not r.passed), None)
        if failed is not None:
            rep.verdict, rep.reason = Verdict.FAILED, f"Outcome check failed: {failed.check.describe()} [{failed.detail}]"
            return
        if sc.outcomes and self.gateway is not None:
            evidence = (
                f"FINAL PAGE\n{obs.render()}\n\nTYPED CHECKS\n"
                + ("\n".join(f"- {'PASS' if r.passed else 'FAIL'} {r.check.describe()}" for r in rep.checks) or "(none)")
                + "\n\nDEFECTS DETECTED\n"
                + ("\n".join(f"- [{s.kind}] {s.message}" for s in rep.signals) or "(none)")
            )
            rep.outcomes = await judge_outcomes(self.gateway, outcomes=sc.outcomes, evidence=evidence, budget=self.budget)
            verdicts = {o.get("verdict") for o in rep.outcomes}
            if "contradicted" in verdicts:
                rep.verdict, rep.reason = Verdict.FAILED, next(o["reason"] for o in rep.outcomes if o.get("verdict") == "contradicted")
            elif "not_shown" in verdicts or not rep.outcomes:
                rep.verdict, rep.reason = Verdict.INCONCLUSIVE, "The evidence does not prove every expected outcome."
            else:
                rep.verdict = Verdict.PASSED
            return
        if sc.outcomes and not typed:
            rep.verdict, rep.reason = Verdict.INCONCLUSIVE, "Plain-English outcomes need a model to judge and none is configured; add typed checks."
            return
        rep.verdict = Verdict.PASSED
        rep.reason = "All steps completed and every typed check passed." if typed else "All steps completed."
