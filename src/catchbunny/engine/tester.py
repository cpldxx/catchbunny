"""The tester loop: one mission, one browser session, one brain.

    observe -> decide -> apply -> observe -> ...

The loop is the same for every brain. All of a decision's intents are applied
against the observation the brain actually saw (refs are never silently
re-bound to a newer DOM), every action passes the policy guard first, and
every action is recorded with a durable element signature so the trace can be
replayed in a fresh browser later. That trace is the unit everything
downstream works on: verification, minimisation, learned steps, exports.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..brain.base import Brain, Feedback, Intent, IntentResult
from ..browser.observe import Observation
from ..browser.session import ActResult, Session, dead_click_signal, policy_violation
from ..budget import Budget
from ..domain import Action, Check, CheckResult, LearnedStep, Mission, Policy, Scenario, Signal, Step, Verdict, now, step_hash
from .productmap import ProductMap

Emit = Callable[[str, dict[str, Any]], None]


@dataclass(slots=True)
class MissionResult:
    mission: Mission
    trace: list[Step] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    learned: list[LearnedStep] = field(default_factory=list)
    step_results: dict[int, bool] = field(default_factory=dict)
    final: Observation | None = None

    def signals(self) -> list[Signal]:
        return [s for step in self.trace for s in step.signals]


class Tester:
    def __init__(
        self,
        *,
        mission: Mission,
        session: Session,
        brain: Brain,
        pmap: ProductMap,
        budget: Budget,
        policy: Policy,
        emit: Emit,
        screenshots: str = "on_signal",
        scenario: Scenario | None = None,
        run_id: str = "",
    ) -> None:
        self.mission, self.session, self.brain = mission, session, brain
        self.pmap, self.budget, self.policy, self.emit = pmap, budget, policy, emit
        self.screenshots = screenshots
        self.scenario = scenario
        self.run_id = run_id
        self.result = MissionResult(mission=mission)
        self._step_actions: list[Action] = []  # actions since the last scenario-step boundary
        self._step_checks: list[Check] = []

    # ------------------------------------------------------------------ loop

    async def run(self, *, start: bool = True) -> MissionResult:
        m = self.mission
        m.status, m.started_at, m.brain = "running", now(), self.brain.name
        self.emit("mission.started", {"mission": m.model_dump(mode="json")})
        try:
            if start:
                opened = await self._do(Action(kind="navigate", value=m.start_path, note="Open the start page"), live=False)
                self._step_actions.clear()  # the opening navigation is not part of any scenario step
                if opened is not None and not opened.ok:
                    self._finish(Verdict.BLOCKED, f"Could not open {m.start_path}: {opened.error}")
            obs = await self._observe() if m.verdict is None else None
            self._publish(0)
            feedback = Feedback(signals=self.result.signals())
            silent = 0
            while m.verdict is None:
                if self.budget.exhausted:
                    self._finish(Verdict.INCONCLUSIVE, f"Stopped: {self.budget.stop_reason}.")
                    break
                if m.steps_taken >= m.max_steps:
                    self._finish(Verdict.INCONCLUSIVE, "Stopped: mission step limit reached.")
                    break
                assert obs is not None
                intents = await self.brain.decide(obs, feedback)
                if not intents:
                    silent += 1
                    if silent >= 2:
                        self._finish(Verdict.INCONCLUSIVE, "The brain stopped choosing actions.")
                        break
                    feedback = Feedback(results=[IntentResult(ok=False, message="No action was chosen. Choose one, or finish.")])
                    continue
                silent = 0
                feedback, obs = await self._apply(intents, obs)
        except asyncio.CancelledError:
            self._finish(Verdict.INCONCLUSIVE, "Canceled.")
            raise
        except Exception as exc:  # a broken page or brain ends this mission, never the run
            self._finish(Verdict.BLOCKED, f"Tester error: {type(exc).__name__}: {exc}"[:300])
        finally:
            m.status, m.finished_at = "done", now()
            m.signals = len(self.result.signals())
            self.emit("mission.finished", {"mission": m.model_dump(mode="json"), "usage": self.brain.usage().as_dict()})
        return self.result

    def _finish(self, verdict: Verdict, summary: str) -> None:
        if self.mission.verdict is None:
            self.mission.verdict, self.mission.summary = verdict, summary

    # ------------------------------------------------------------------ one decision

    async def _apply(self, intents: list[Intent], obs: Observation) -> tuple[Feedback, Observation]:
        first = len(self.result.trace)
        results: list[IntentResult] = []
        current, fresh = obs, True
        last: tuple[Action, ActResult] | None = None
        quiet = True  # did the whole decision cause no navigation and no dialog?
        for it in intents:
            if it.kind == "act" and it.action is not None:
                action = it.action
                if action.target is not None and action.target.ref:
                    el = obs.find(action.target.ref)
                    if el is None:
                        results.append(IntentResult(id=it.id, ok=False, message=f"Unknown ref {action.target.ref}; use a ref from the latest observation."))
                        continue
                    if el.disabled:
                        results.append(IntentResult(id=it.id, ok=False, message=f"{el.label()} is disabled."))
                        continue
                    action = action.model_copy(update={"target": el.sig()})
                if action.to is not None and action.to.ref:
                    onto = obs.find(action.to.ref)
                    if onto is None:
                        results.append(IntentResult(id=it.id, ok=False, message=f"Unknown ref {action.to.ref}; use a ref from the latest observation."))
                        continue
                    action = action.model_copy(update={"to": onto.sig()})
                if why := policy_violation(self.policy, action, self.session.origin):
                    results.append(IntentResult(id=it.id, ok=False, message=f"Blocked by policy: {why}."))
                    continue
                if not fresh and action.target is not None and self.session.page.url != obs.url:
                    results.append(IntentResult(id=it.id, ok=False, message="Skipped: the page changed. Act on the new observation."))
                    continue
                done = await self._do(action, live=True, thought=it.thought)
                if done is None:
                    results.append(IntentResult(id=it.id, ok=False, message=f"Not executed: {self.budget.stop_reason}."))
                    break
                fresh, last = False, (action, done)
                quiet = quiet and not (done.navigated or done.dialogs)
                results.append(IntentResult(id=it.id, ok=done.ok, message="ok" if done.ok else done.error))
            elif it.kind == "check" and it.check is not None:
                if not fresh:
                    current, fresh = await self._observe(), True
                res = await self.session.run_check(it.check, current, self.result.signals())
                self.result.checks.append(res)
                if res.passed:
                    self._step_checks.append(it.check)
                results.append(IntentResult(id=it.id, ok=res.passed, message=f"{'PASS' if res.passed else 'FAIL'} {it.check.describe()} [{res.detail}]"))
            elif it.kind == "report" and it.report is not None:
                if not fresh:
                    current, fresh = await self._observe(), True
                results.append(await self._report(it, current))
            elif it.kind == "step_done":
                results.append(self._step_done(it))
            elif it.kind == "finish":
                self._finish(it.verdict or Verdict.INCONCLUSIVE, it.summary)
                results.append(IntentResult(id=it.id))
                break
        if not fresh:
            current = await self._observe()
        if last is not None and self.result.trace:
            action, res = last
            if quiet and (dead := dead_click_signal(action, res, obs, current)) is not None:
                self.session.note(dead)
                self.result.trace[-1].signals += self.session.drain(self.result.trace[-1].index)
            self.pmap.record(obs.state_id, action, current.state_id)
            self.result.trace[-1].state_after = current.state_id
        new_signals = [s for step in self.result.trace[first:] for s in step.signals]
        if self.result.trace[first:] and (self.screenshots == "every_step" or (new_signals and self.screenshots == "on_signal")):
            self.result.trace[-1].screenshot = await self.session.screenshot()
        self._publish(first)
        return Feedback(results=results, signals=new_signals), current

    async def _do(self, action: Action, *, live: bool, thought: str = "") -> ActResult | None:
        if not self.budget.take_step():
            return None
        url_before = self.session.page.url
        res = await self.session.act(action, live=live)
        step = Step(
            index=len(self.result.trace),
            action=action,
            ok=res.ok,
            error=res.error,
            healed=res.healed,
            url_before=url_before,
            url_after=self.session.page.url,
            duration_ms=res.duration_ms,
            thought=thought,
        )
        step.signals = self.session.drain(step.index)
        self.result.trace.append(step)
        self.mission.steps_taken += 1
        if res.ok:
            self._step_actions.append(action)
        return res

    async def _observe(self) -> Observation:
        obs = await self.session.observe()
        if self.result.trace:  # page-level defects belong to the action that revealed them
            last = self.result.trace[-1]
            last.signals += self.session.drain(last.index)
        self.pmap.observe(obs)
        self.result.final = obs
        return obs

    def _publish(self, first: int) -> None:
        for step in self.result.trace[first:]:
            self.emit(
                "step",
                {
                    "mission_id": self.mission.id,
                    "index": step.index,
                    "action": step.action.describe(),
                    "ok": step.ok,
                    "error": step.error,
                    "url": step.url_after,
                    "ms": step.duration_ms,
                    "screenshot": step.screenshot,
                    "signals": [{"kind": s.kind, "message": s.message, "signature": s.signature} for s in step.signals],
                },
            )

    # ------------------------------------------------------------------ model-reported bugs and scenario steps

    async def _report(self, it: Intent, obs: Observation) -> IntentResult:
        draft = it.report
        assert draft is not None
        if not self.result.trace:
            return IntentResult(id=it.id, ok=False, message="Nothing has been done yet.")
        detail: dict[str, Any] = {"title": draft.title, "expected": draft.expected, "actual": draft.actual, "severity": draft.severity.value}
        if draft.check is not None:
            res = await self.session.run_check(draft.check, obs, self.result.signals())
            if not res.evaluable:
                return IntentResult(
                    id=it.id,
                    ok=False,
                    message="Not recorded: "
                    + (f'there is no {draft.check.role or "control"} named "{draft.check.name}" on this page' if draft.check.well_formed() else "the check is empty - it does not say which text or control it is about")
                    + ", so it cannot be evaluated. "
                    "Name a control exactly as the outline shows it, or use text_absent with the wrong text that is visible.",
                )
            if res.passed:
                return IntentResult(
                    id=it.id,
                    ok=False,
                    message=f"Not recorded: {draft.check.describe()} PASSES on this page, so it does not demonstrate the bug. "
                    "Supply a check that fails while the bug is present.",
                )
            detail["check"] = draft.check.model_dump()
        last = self.result.trace[-1]
        last.signals.append(Signal(kind="agent_report", message=draft.title, url=obs.url, detail=detail, step=last.index))
        if draft.check is not None and not draft.check.witnesses():
            return IntentResult(
                id=it.id,
                message="Recorded as UNPROVEN: the check fails because something is missing, and a replay cannot tell a missing thing from a wrong expectation. "
                "If the page shows something wrong, report that instead: text_absent for wrong text that is visible, value_equals for a field holding the wrong value.",
            )
        return IntentResult(id=it.id, message="Recorded. It will be re-verified in a fresh browser.")

    def _step_done(self, it: Intent) -> IntentResult:
        if self.scenario is None or not 0 <= it.step_index < len(self.scenario.steps):
            return IntentResult(id=it.id, ok=False, message="No such scenario step.")
        self.result.step_results[it.step_index] = it.passed
        if it.passed and self._step_actions:
            self.result.learned.append(
                LearnedStep(
                    step_hash=step_hash(self.scenario.steps[it.step_index]),
                    actions=self._step_actions,
                    checks=self._step_checks,
                    source_run=self.run_id,
                )
            )
        self._step_actions, self._step_checks = [], []
        return IntentResult(id=it.id, message=f"Step {it.step_index + 1} marked {'passed' if it.passed else 'failed'}.")
