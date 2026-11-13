"""Model-driven testers, planner and outcome judge - for any model provider.

The tester loop, the tool vocabulary and the prompts here are provider-neutral.
A `Provider` adapts them to one model API and owns everything specific to it:
the wire format, the cost model, and the context strategy that cost model
calls for.

    ModelBrain --+
    planner    --+--> LLMGateway (one semaphore, one ledger) --> Provider
    judge      --+                                               |- AnthropicProvider   brain/claude.py
                                                                 |- OllamaProvider      brain/ollama.py

The transcript is the shared contract. It is a list of messages whose content
is a list of blocks - `text`, `tool_use`, `tool_result` - and it is only ever
appended to. A provider may put blocks of its own in an assistant turn (thinking)
and receives them back untouched. What a provider *sends* is its own business:
Claude gets the transcript verbatim, because an unchanged prefix is a cache hit;
a local model gets a view with stale page outlines masked, because its context
is small and nothing is billed.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..browser import samples
from ..browser.observe import Observation
from ..budget import Budget, Usage
from ..config import Settings
from ..domain import Action, Check, ElementSig, Severity, Verdict
from .base import Brief, Feedback, FindingDraft, Intent

PAGE_MARK = "CURRENT PAGE\n"  # precedes every page outline in a user turn; providers that mask old outlines split on it

_CHECK_KINDS = ["url_contains", "url_matches", "title_contains", "text_visible", "text_absent", "element_visible", "element_absent", "value_equals", "no_http_error"]


def _tool(name: str, description: str, props: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": props, "required": list(props), "additionalProperties": False},
    }


_S = {"type": "string"}
_REF = {"type": "string", "description": "Ref of a control in the latest page outline, e.g. e12."}

# Sorted by name and never mutated: tools render first in the prompt, so any
# change here would invalidate the cache for every tester.
TOOLS: list[dict[str, Any]] = sorted(
    [
        _tool("click", "Click a control.", {"ref": _REF, "why": _S}),
        _tool("fill", "Replace the contents of a text field.", {"ref": _REF, "text": _S}),
        _tool(
            "fill_credential",
            "Fill a field with the persona's username or password. The value is injected by the supervisor; you never see it.",
            {"ref": _REF, "field": {"type": "string", "enum": ["username", "password"]}},
        ),
        _tool("select_option", "Choose an option of a <select> by value.", {"ref": _REF, "value": _S}),
        _tool("press_key", "Press a key on the focused element, e.g. Enter, Escape, Tab.", {"key": _S}),
        _tool(
            "upload_file",
            "Give a file control (role `file`) a sample file. You choose the kind; the supervisor supplies the file.",
            {"ref": _REF, "kind": {"type": "string", "enum": sorted(samples.KINDS)}},
        ),
        _tool("drag", "Drag one control and drop it on another, e.g. a card onto a column that accepts drops.", {"ref": _REF, "onto_ref": _REF}),
        _tool("navigate", "Open a path of the app under test, e.g. /cart. Never another site.", {"path": _S}),
        _tool("go_back", "Go back one page in history.", {}),
        _tool(
            "assert_check",
            "Evaluate a typed check against the live page right now and get PASS or FAIL. "
            "Use role+name for element_* and value_equals; use value for everything else. Leave unused fields empty.",
            {"kind": {"type": "string", "enum": _CHECK_KINDS}, "value": _S, "role": _S, "name": _S},
        ),
        _tool(
            "report_bug",
            "Record a defect the detectors cannot see. Give a typed check that a correct app would PASS and that FAILS now because of "
            "something the page shows: text_absent for wrong text that is visible, value_equals for a field holding the wrong value, url_* for the wrong page. "
            "The report is rejected if the check passes or names a control that is not on the page. A check that fails only because something is "
            "missing (text_visible, element_visible) is recorded as unproven. Use check_kind 'none' only when no typed check can express it.",
            {
                "title": _S,
                "expected": _S,
                "actual": _S,
                "severity": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
                "check_kind": {"type": "string", "enum": [*_CHECK_KINDS, "none"]},
                "check_value": _S,
                "check_role": _S,
                "check_name": _S,
            },
        ),
        _tool(
            "complete_step",
            "Scenario missions only: mark scenario step `index` (1-based) as done, passed or failed.",
            {"index": {"type": "integer"}, "passed": {"type": "boolean"}, "note": _S},
        ),
        _tool(
            "finish",
            "End the mission. passed: the flow works / the charter is covered with no unrecorded problem. failed: a defect blocks the flow. "
            "blocked: you could not proceed (login, policy, missing data). inconclusive: you ran out of room to tell.",
            {"verdict": {"type": "string", "enum": ["passed", "failed", "blocked", "inconclusive"]}, "summary": _S},
        ),
    ],
    key=lambda t: t["name"],
)

SYSTEM = """You are one tester in a swarm that tests a web application the way its real users use it. You drive a single browser tab. In each mission you either establish that a flow works or find real defects and prove them.

## What you see
Each turn you receive an outline of the current page: URL, title, headings, one line per control as `[ref] role "name"` with its state, then the visible text. Refs are valid only for the most recent outline. The outline is the whole page, not just the viewport, so you never need to scroll.

Detectors run continuously beside you and record, without your help: server errors, uncaught exceptions, console errors, failed requests, broken images, unlabelled controls, horizontal overflow, and controls that do nothing when clicked. When one fires you will see it under DETECTED. Do not re-report those; use them as leads.

## What only you can find
Everything that needs understanding: a total that does not add up, a count that contradicts the list beside it, a change that does not persist after reload, validation that lets bad input through, a step a user cannot complete, a state the UI should not allow, data that belongs to someone else. Pursue the paths a hurried or careless user would take: empty and extreme inputs, doing things twice, going back, skipping a step.

## Proof, not opinion
A defect counts only when a typed check demonstrates it. `report_bug` takes a check that a correct app would pass and that fails now; if your check passes, the report is rejected. Point the check at what is wrong on the page, not at what you hoped to see: a wrong total is proved by `text_absent` on the wrong figure that is displayed, not by `text_visible` on the right one. Every recorded defect is then replayed from scratch in a fresh browser, and one that does not reproduce is discarded, so report what you can show, at the moment it is visible on the page. Likewise, before finishing a flow as passed, confirm its outcome with `assert_check` rather than assuming a click worked.

## Operating rules
- You act as the persona you are given. To sign in, use `fill_credential`; you are never shown the password and must not ask for it.
- A supervisor enforces the mission's policy. An action answered with "Blocked by policy" is not available to you; move on rather than looking for another route to the same effect.
- Stay inside the application under test.
- You may call several tools in one turn when the later ones do not depend on how the page responds to the earlier ones (fill three fields, then click submit). After anything that loads or changes the page, stop and read the new outline.
- You have a limited number of steps. Spend them on the mission's charter, and call `finish` as soon as it is covered.

## Scenario missions
A scenario gives numbered steps and expected outcomes. Perform the steps in order, calling `complete_step` after each. If a step cannot be completed, mark it failed, record the reason with `report_bug` when it is a defect, and finish as failed. When all steps are done, verify each expected outcome with `assert_check`, then finish."""


# --------------------------------------------------------------------------- provider port


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(slots=True)
class Reply:
    """One model turn, in provider-neutral form."""

    content: list[Any]  # appended to the transcript exactly as received
    calls: list[ToolCall] = field(default_factory=list)
    text: str = ""
    stop: str = "end"  # end | tool_use | max_tokens | refusal
    usage: Usage = field(default_factory=Usage)
    detail: str = ""  # refusal category, when the provider gives one


class ModelError(Exception):
    """A request that failed for good. `verdict` is what it means for the mission:
    BLOCKED when retrying cannot help (credentials, a missing model), INCONCLUSIVE when it might."""

    def __init__(self, verdict: Verdict, message: str) -> None:
        super().__init__(message)
        self.verdict, self.message = verdict, message


class Provider(Protocol):
    name: str  # recorded on every mission it drives: "claude", "ollama"
    model: str
    concurrency: int  # requests allowed in flight at once

    async def unavailable(self) -> str:
        """Why this provider cannot be used right now, in words a user can act on; "" when it can."""

    async def warm(self) -> Reply:
        """Make the testers' shared prefix cheap before the swarm fans out."""

    async def tester_turn(self, messages: list[dict[str, Any]]) -> Reply:
        """One turn of a tester conversation: SYSTEM + TOOLS + the transcript."""

    async def structured(self, system: str, prompt: str, schema: dict[str, Any]) -> Reply:
        """One answer whose `text` is JSON matching `schema`."""

    async def close(self) -> None: ...


class LLMGateway:
    """The single door to one provider: bounded concurrency and one usage ledger."""

    def __init__(self, settings: Settings, provider: Provider) -> None:
        self.s, self.provider = settings, provider
        self._gate = asyncio.Semaphore(max(1, provider.concurrency))
        self.total = Usage()

    async def _through(self, request: Awaitable[Reply], budget: Budget | None) -> Reply:
        async with self._gate:
            reply = await request
        self.total.add(reply.usage)
        if budget is not None:
            budget.llm.add(reply.usage)
        return reply

    async def tester_turn(self, messages: list[dict[str, Any]], budget: Budget | None = None) -> Reply:
        return await self._through(self.provider.tester_turn(messages), budget)

    async def prewarm(self, budget: Budget | None = None) -> bool:
        try:
            await self._through(self.provider.warm(), budget)
            return True
        except ModelError:
            return False  # best effort: the first tester pays for the prefix instead

    async def structured(self, *, system: str, prompt: str, schema: dict[str, Any], budget: Budget | None = None) -> dict[str, Any] | None:
        """One schema-constrained JSON answer; None on refusal, truncation or error."""
        try:
            reply = await self._through(self.provider.structured(system, prompt, schema), budget)
        except ModelError:
            return None
        if reply.stop in ("refusal", "max_tokens"):
            return None
        try:
            data = json.loads(reply.text)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    async def close(self) -> None:
        await self.provider.close()


# --------------------------------------------------------------------------- tester


def render_brief(b: Brief) -> str:
    m, p = b.mission, b.persona
    lines = [
        f"MISSION: {m.title}",
        f"PERSONA: {p.get('name', 'visitor')} - {p.get('role', '')}. {p.get('goals', '')}".strip(),
        f"CREDENTIAL AVAILABLE: {'yes (already signed in when the mission starts, if sign-in succeeded)' if p.get('has_credential') else 'no'}",
        f"POLICY: {b.policy.value}",
        f"DEVICE: {m.device}",
        f"STEP LIMIT: {m.max_steps}",
    ]
    if b.areas:
        lines.append(f"STAY WITHIN: {', '.join(b.areas)}")
    if b.knowledge:
        lines.append(f"PRODUCT NOTES:\n{b.knowledge}")
    if b.scenario is not None:
        s = b.scenario
        lines.append(f"SCENARIO: {s.name}")
        lines += [f"  {i + 1}. {text}" + ("  (already completed by replay)" if i < b.first_step else "") for i, text in enumerate(s.steps)]
        if s.outcomes:
            lines.append("EXPECTED OUTCOMES:")
            lines += [f"  - {o}" for o in s.outcomes]
        if b.first_step:
            lines.append(f"Continue from step {b.first_step + 1}; the browser is already where the earlier steps left it.")
    else:
        if b.goal:
            lines.append(f"RUN GOAL: {b.goal}")
        lines.append(f"CHARTER: {m.charter or 'Explore this area and look for defects.'}")
    return "\n".join(lines)



_NUDGE = "You did not call a tool. Every turn must be one or more tool calls: act on the page, or call `finish` if the mission is done or cannot continue."
_ATTEMPTS = 3  # per decision: the first try plus two corrections


class ModelBrain:
    """A tester whose decisions come from a model, through whichever provider the gateway wraps."""

    def __init__(self, brief: Brief, gateway: LLMGateway, budget: Budget) -> None:
        self.brief, self.gateway, self.budget = brief, gateway, budget
        self.name = gateway.provider.name
        self.messages: list[dict[str, Any]] = []
        self._pending: list[str] = []  # tool_use ids awaiting a tool_result
        self._invalid: dict[str, str] = {}
        self._usage = Usage()

    def usage(self) -> Usage:
        return self._usage

    async def decide(self, obs: Observation, feedback: Feedback) -> list[Intent]:
        self.messages.append({"role": "user", "content": self._user_turn(obs, feedback)})
        for _ in range(_ATTEMPTS):
            try:
                reply = await self.gateway.tester_turn(self.messages, self.budget)
            except ModelError as exc:
                return [self._stop(exc.verdict, exc.message)]
            self._usage.add(reply.usage)
            if reply.stop == "refusal":  # always check before reading content
                return [self._stop(Verdict.BLOCKED, f"The model declined this mission ({reply.detail or 'no category'}).")]
            # Provider blocks (thinking) must go back unchanged: append the content as received.
            self.messages.append({"role": "assistant", "content": reply.content})
            self._pending, self._invalid = [c.id for c in reply.calls], {}
            if reply.stop == "max_tokens" and not reply.calls:
                return [self._stop(Verdict.INCONCLUSIVE, "The model's turn was cut off by max_tokens.")]
            intents: list[Intent] = []
            for call in reply.calls:
                try:
                    intents.append(self._intent(call.id, call.name, call.input, reply.text[:300]))
                except (KeyError, ValueError, TypeError, AttributeError) as exc:
                    self._invalid[call.id] = f"Invalid tool input: {exc!s}"
            if intents:
                return intents
            # Nothing usable came back (prose only, or every call malformed). Say why in the
            # transcript and ask again; small local models need this far more often than Claude.
            self.messages.append({"role": "user", "content": self._correction()})
        return [self._stop(Verdict.INCONCLUSIVE, "The model did not produce a usable tool call.")]

    def _stop(self, verdict: Verdict, summary: str) -> Intent:
        self._pending = []
        return Intent(kind="finish", verdict=verdict, summary=summary)

    def _correction(self) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = [
            {"type": "tool_result", "tool_use_id": tool_id, "content": self._invalid.get(tool_id, "Not executed."), "is_error": True} for tool_id in self._pending
        ]
        self._pending = []
        blocks.append({"type": "text", "text": "Call the tool again with valid input." if blocks else _NUDGE})
        return blocks

    def _user_turn(self, obs: Observation, feedback: Feedback) -> list[dict[str, Any]]:
        page = f"{PAGE_MARK}{obs.render()}"
        detected = "".join(f"\n- [{s.kind}] {s.message}" for s in feedback.signals)
        seen = f"DETECTED (recorded automatically):{detected}\n\n" if detected else ""
        if not self.messages:
            return [{"type": "text", "text": f"{render_brief(self.brief)}\n\n{seen}{page}"}]
        by_id = {r.id: r for r in feedback.results}
        blocks: list[dict[str, Any]] = []
        for tool_id in self._pending:  # every tool_use gets exactly one tool_result, all in one message
            r = by_id.get(tool_id)
            text = r.message if r else self._invalid.get(tool_id, "Not executed.")
            blocks.append({"type": "tool_result", "tool_use_id": tool_id, "content": text, "is_error": not (r and r.ok)})
        notes = [r.message for r in feedback.results if not r.id and not r.ok]
        tail = ("\n".join(notes) + "\n\n" if notes else "") + seen + page
        blocks.append({"type": "text", "text": tail})
        return blocks

    @staticmethod
    def _intent(tool_id: str, name: str, i: dict[str, Any], thought: str) -> Intent:
        def act(action: Action) -> Intent:
            return Intent(kind="act", id=tool_id, action=action, thought=thought)

        def target() -> ElementSig:
            return ElementSig(ref=str(i["ref"]))

        match name:
            case "click":
                return act(Action(kind="click", target=target(), note=str(i.get("why", ""))))
            case "fill":
                return act(Action(kind="fill", target=target(), value=str(i["text"])))
            case "fill_credential":
                return act(Action(kind="fill", target=target(), secret=str(i["field"])))
            case "select_option":
                return act(Action(kind="select", target=target(), value=str(i["value"])))
            case "press_key":
                return act(Action(kind="press", value=str(i["key"])))
            case "upload_file":
                return act(Action(kind="upload", target=target(), value=f"sample.{samples.KINDS[str(i['kind'])]}"))
            case "drag":
                return act(Action(kind="drag", target=target(), to=ElementSig(ref=str(i["onto_ref"]))))
            case "navigate":
                return act(Action(kind="navigate", value=str(i["path"])))
            case "go_back":
                return act(Action(kind="back"))
            case "assert_check":
                return Intent(kind="check", id=tool_id, check=Check(kind=i["kind"], value=i.get("value", ""), role=i.get("role", ""), name=i.get("name", "")))
            case "report_bug":
                check = None
                if i.get("check_kind", "none") != "none":
                    check = Check(kind=i["check_kind"], value=i.get("check_value", ""), role=i.get("check_role", ""), name=i.get("check_name", ""))
                draft = FindingDraft(title=i["title"], expected=i.get("expected", ""), actual=i.get("actual", ""), severity=Severity(i["severity"]), check=check)
                return Intent(kind="report", id=tool_id, report=draft)
            case "complete_step":
                return Intent(kind="step_done", id=tool_id, step_index=int(i["index"]) - 1, passed=bool(i["passed"]), summary=str(i.get("note", "")))
            case "finish":
                return Intent(kind="finish", id=tool_id, verdict=Verdict(i["verdict"]), summary=str(i.get("summary", "")))
        raise ValueError(f"unknown tool {name}")


# --------------------------------------------------------------------------- planner and judge

_PLANNER_SYSTEM = """You plan exploratory testing for a web application. Given a goal and a map of the screens found so far, write test charters for a swarm of independent testers. Each charter is one tester's assignment: a specific risk to probe, concrete enough to act on, different enough from the others that the testers do not duplicate work. Prefer charters about behaviour that can be wrong in ways an automated error detector would miss (calculations, state, permissions, validation, multi-step flows, edge cases). Use only the personas given, and start paths that exist in the map."""

_CHARTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "charters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": _S, "charter": _S, "persona": _S, "start_path": _S},
                "required": ["title", "charter", "persona", "start_path"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["charters"],
    "additionalProperties": False,
}

_JUDGE_SYSTEM = """You are an independent reviewer of a finished browser test. You did not run it. Decide, for each expected outcome, whether the evidence proves it held. The evidence is the final page outline, the typed checks that were evaluated, and the defects detected during the run. Judge only from the evidence: an outcome the evidence does not show is not proven, however plausible."""

_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "outcomes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"outcome": _S, "verdict": {"type": "string", "enum": ["proven", "contradicted", "not_shown"]}, "reason": _S},
                "required": ["outcome", "verdict", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["outcomes"],
    "additionalProperties": False,
}


async def plan_charters(gateway: LLMGateway, *, goal: str, map_brief: str, personas: list[dict[str, Any]], knowledge: str, count: int, budget: Budget | None = None) -> list[dict[str, str]]:
    prompt = (
        f"GOAL: {goal or 'Find the most damaging defects a real user could hit.'}\n\n"
        f"PERSONAS:\n" + "\n".join(f"- {p['name']}: {p.get('role', '')} {p.get('goals', '')}" for p in personas) + "\n\n"
        f"SCREENS FOUND SO FAR (path | title | headings | controls):\n{map_brief}\n\n"
        + (f"PRODUCT NOTES:\n{knowledge}\n\n" if knowledge else "")
        + f"Write {count} charters."
    )
    data = await gateway.structured(system=_PLANNER_SYSTEM, prompt=prompt, schema=_CHARTER_SCHEMA, budget=budget)
    return list(data.get("charters", []))[:count] if data else []


async def judge_outcomes(gateway: LLMGateway, *, outcomes: list[str], evidence: str, budget: Budget | None = None) -> list[dict[str, str]]:
    prompt = "EXPECTED OUTCOMES:\n" + "\n".join(f"- {o}" for o in outcomes) + f"\n\nEVIDENCE:\n{evidence}"
    data = await gateway.structured(system=_JUDGE_SYSTEM, prompt=prompt, schema=_JUDGE_SCHEMA, budget=budget)
    return list(data.get("outcomes", [])) if data else []
