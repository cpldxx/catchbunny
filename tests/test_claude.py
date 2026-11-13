"""The Claude adapter, driven by a fake client: no network, no key.

The fake plays the model. It reads the page outline exactly as the real model
would (from the request's messages), so these tests exercise the full request
contract and the full tester loop - including a semantic bug the heuristic
brain cannot see, proved by a typed check and re-verified by replay.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

from conftest import shop_project

from catchbunny.brain.base import Brief, Feedback, IntentResult
from catchbunny.brain.claude import FALLBACK_BETA, SYSTEM_BLOCKS, TOOLS, AnthropicProvider
from catchbunny.brain.llm import LLMGateway, ModelBrain
from catchbunny.browser.observe import Observation
from catchbunny.budget import Budget
from catchbunny.config import Settings
from catchbunny.domain import FindingStatus, Mission, Policy, RunKind, RunSpec, Verdict
from catchbunny.engine.orchestrator import App


def tool(tool_name: str, /, **inp: Any) -> SimpleNamespace:
    tool.n = getattr(tool, "n", 0) + 1  # type: ignore[attr-defined]
    return SimpleNamespace(type="tool_use", id=f"toolu_{tool.n}", name=tool_name, input=inp)  # type: ignore[attr-defined]


def reply(*blocks: SimpleNamespace, stop: str = "tool_use", read: int = 900, write: int = 0) -> SimpleNamespace:
    usage = SimpleNamespace(input_tokens=120, output_tokens=40, cache_read_input_tokens=read, cache_creation_input_tokens=write)
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=usage, stop_details=None)


class FakeClient:
    """Stands in for AsyncAnthropic. `script` maps a request to a response."""

    def __init__(self, script: Any) -> None:
        self.script = script
        self.requests: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._beta))
        self.messages = SimpleNamespace(create=self._plain)

    async def _beta(self, **kw: Any) -> Any:
        self.requests.append({"endpoint": "beta", **kw, "messages": list(kw["messages"])})
        return self.script(kw)

    async def _plain(self, **kw: Any) -> Any:
        self.requests.append({"endpoint": "plain", **kw, "messages": list(kw["messages"])})
        return self.script(kw)


def last_text(request: dict[str, Any]) -> str:
    content = request["messages"][-1]["content"]
    return "\n".join(b["text"] for b in content if b.get("type") == "text")


def ref(text: str, role: str, name: str) -> str:
    return re.search(rf'\[(e\d+)\] {role} "{re.escape(name)}', text).group(1)  # type: ignore[union-attr]


def _brief() -> Brief:
    return Brief(mission=Mission(title="Probe the cart", charter="Check quantities."), base_url="http://app.test", policy=Policy.SAFE_WRITES, persona={"name": "visitor"})


def _page() -> Observation:
    raw = {"url": "http://app.test/p", "title": "P", "elements": [{"ref": "e1", "role": "button", "name": "Go", "tag": "button", "attrs": {}, "text": "Go", "near": "", "css": "button"}]}
    return Observation.from_raw(raw)


async def test_request_contract_and_append_only_conversation() -> None:
    turns = iter(
        [
            reply(SimpleNamespace(type="thinking", thinking="", signature="s"), tool("click", ref="e1", why="try it"), tool("assert_check", kind="url_contains", value="/p", role="", name="")),
            reply(SimpleNamespace(type="text", text="done"), tool("finish", verdict="passed", summary="ok")),
        ]
    )
    client = FakeClient(lambda _: next(turns))
    gateway = LLMGateway(Settings(), AnthropicProvider(Settings(), client=client))
    budget = Budget(max_steps=10, max_seconds=60, max_usd=5)
    brain = ModelBrain(_brief(), gateway, budget)

    intents = await brain.decide(_page(), Feedback())
    assert [i.kind for i in intents] == ["act", "check"] and intents[0].action.target.ref == "e1"  # type: ignore[union-attr]
    feedback = Feedback(results=[IntentResult(id=intents[0].id, ok=True, message="ok"), IntentResult(id=intents[1].id, ok=False, message="FAIL url_contains")])
    done = await brain.decide(_page(), feedback)
    assert done[0].kind == "finish" and done[0].verdict is Verdict.PASSED

    first, second = client.requests
    assert first["endpoint"] == "beta" and first["betas"] == [FALLBACK_BETA] and first["fallbacks"] == "default"
    assert first["model"] == "claude-opus-5-5" and first["output_config"] == {"effort": "low"} and "thinking" not in first
    assert first["system"] is SYSTEM_BLOCKS and first["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert first["tools"] is TOOLS and [t["name"] for t in TOOLS] == sorted(t["name"] for t in TOOLS)
    assert all(t["strict"] and t["input_schema"]["additionalProperties"] is False and set(t["input_schema"]["required"]) == set(t["input_schema"]["properties"]) for t in TOOLS)
    assert first["cache_control"] == {"type": "ephemeral"}
    # History is only ever appended to: turn 2's messages start with turn 1's, byte for byte.
    assert second["messages"][: len(first["messages"])] == first["messages"]
    assistant = second["messages"][1]
    assert assistant["role"] == "assistant" and assistant["content"][0].type == "thinking", "thinking blocks go back unchanged"
    results = [b for b in second["messages"][2]["content"] if b["type"] == "tool_result"]
    assert [r["tool_use_id"] for r in results] == [b.id for b in assistant["content"] if b.type == "tool_use"], "one result per call, in one message"
    assert [r["is_error"] for r in results] == [False, True]
    assert "CURRENT PAGE" in second["messages"][2]["content"][-1]["text"]
    assert budget.llm.requests == 2 and budget.llm.usd > 0 and budget.llm.as_dict()["cache_hit_rate"] > 0.8


async def test_refusal_and_unknown_models_are_handled() -> None:
    client = FakeClient(lambda _: reply(stop="refusal"))
    settings = Settings()
    settings.model = "claude-haiku-5-5"  # no server-side fallback for this model
    brain = ModelBrain(_brief(), LLMGateway(settings, AnthropicProvider(settings, client=client)), Budget(max_steps=5, max_seconds=5))
    (stop,) = await brain.decide(_page(), Feedback())
    assert stop.kind == "finish" and stop.verdict is Verdict.BLOCKED and "declined" in stop.summary
    assert client.requests[0]["endpoint"] == "plain" and "fallbacks" not in client.requests[0]


async def test_prewarm_writes_the_exact_prefix_real_traffic_reads() -> None:
    client = FakeClient(lambda _: reply(stop="max_tokens", read=0, write=1400))
    gateway = LLMGateway(Settings(), AnthropicProvider(Settings(), client=client))
    assert await gateway.prewarm()
    warm = client.requests[0]
    assert warm["max_tokens"] == 0 and warm["system"] is SYSTEM_BLOCKS and warm["tools"] is TOOLS
    assert warm["output_config"] == {"effort": gateway.s.effort} and "cache_control" not in warm
    assert gateway.total.cache_write_tokens == 1400


async def test_model_finds_a_semantic_bug_and_replay_confirms_it(app: App, buggy_shop: str) -> None:
    """End to end with a scripted "model": a negative quantity makes the cart total negative.

    No detector fires for this. The model proves it with a typed check, and the
    engine then reproduces it in fresh browsers before calling it confirmed.
    """

    def model(request: dict[str, Any]) -> SimpleNamespace:
        if request["max_tokens"] == 0:  # the pre-warm
            return reply(stop="max_tokens", read=0, write=1400)
        if "format" in request.get("output_config", {}):  # the planner
            plan = {"charters": [{"title": "Negative quantities", "charter": "Try a negative quantity.", "persona": "visitor", "start_path": "/products/1"}]}
            return reply(SimpleNamespace(type="text", text=json.dumps(plan)), stop="end_turn")
        text = last_text(request)
        url = re.search(r"^URL: (\S+)", text, re.M).group(1)  # type: ignore[union-attr]
        reports = sum(getattr(b, "name", "") == "report_bug" for m in request["messages"] if m["role"] == "assistant" for b in m["content"])
        if url.endswith("/products/1"):
            return reply(tool("fill", ref=ref(text, "spinbutton", "Quantity"), text="-1"), tool("click", ref=ref(text, "button", "Add to cart"), why="submit"))
        if url.endswith("/cart") and "$-12.00" in text and reports < 5:
            report = {"title": "Negative quantity is accepted and makes the cart total negative", "expected": "Quantity below 1 is rejected.", "actual": "Cart total is $-12.00.", "severity": "P1"}
            checks = [
                {"check_kind": "value_equals", "check_value": "$12.00", "check_role": "text", "check_name": "Cart total"},  # no such control: cannot be evaluated
                {"check_kind": "value_equals", "check_value": "-1", "check_role": "", "check_name": ""},  # names no control at all
                {"check_kind": "text_absent", "check_value": "", "check_role": "", "check_name": ""},  # absent what? "" is on every page
                {"check_kind": "text_visible", "check_value": "Total: $12.00", "check_role": "", "check_name": ""},  # fails only because something is missing
                {"check_kind": "text_absent", "check_value": "$-12.00", "check_role": "", "check_name": ""},  # fails because the wrong figure is on the page
            ]
            title = report["title"] if reports == 4 else f"Attempt {reports + 1}: {report['title']}"
            return reply(tool("report_bug", **{**report, "title": title}, **checks[reports]))
        return reply(tool("finish", verdict="failed", summary="Negative totals are possible."))

    app.gateways["claude"] = LLMGateway(app.settings, AnthropicProvider(app.settings, client=FakeClient(model)))
    project = shop_project(buggy_shop)
    project.personas = project.personas[:1]
    spec = RunSpec(kind=RunKind.DEEP_SCAN, brain="claude", testers=2, max_steps=120, max_minutes=2, goal="Find wrong totals")
    run = await app.runs.wait((await app.runs.start(project, spec)).id)
    assert run.error == "", run.error
    findings = await app.store.list("finding", parent=run.id)
    semantic = {f["status"]: f for f in findings if f["kind"] == "agent_report"}
    assert set(semantic) == {FindingStatus.CONFIRMED.value, FindingStatus.UNVERIFIED.value}, [(f["status"], f["title"]) for f in findings]
    f = semantic[FindingStatus.CONFIRMED.value]
    assert f["verification"]["reproduced"] == f["verification"]["runs"] and f["check"]["kind"] == "text_absent" and f["severity"] == "P1"
    assert [a["kind"] for a in f["repro"]] == ["navigate", "fill", "click"] and not f["minimized"], "a model's report keeps the tester's own path: its check is not a complete oracle to shorten against"
    # A check that fails only because something is missing is a claim, not a proof: kept, labelled, never "confirmed".
    claim = semantic[FindingStatus.UNVERIFIED.value]
    assert claim["check"]["kind"] == "text_visible" and claim["verification"]["runs"] == 0 and "something is missing" in claim["evidence"]["note"]
    assert claim["id"] not in run.summary["blocking_findings"] and f["id"] in run.summary["blocking_findings"]
    # What the model was told, once per tool call (the append-only history repeats each answer in later requests).
    told = {b["tool_use_id"]: b["content"] for r in app.gateways["claude"].provider.client.requests for m in r["messages"] if m["role"] == "user" and isinstance(m["content"], list) for b in m["content"] if b.get("type") == "tool_result"}
    answers = list(told.values())
    assert sum("cannot be evaluated" in a for a in answers) == 3, "a check on a missing control, an unnamed one, or an empty text is rejected - never counted as failing"
    assert sum("the check is empty" in a for a in answers) == 2
    assert sum("UNPROVEN" in a for a in answers) == 1 and sum(a.startswith("Recorded. It will be re-verified") for a in answers) == 1
    assert "claude" in run.summary["brains"] and run.summary["budget"]["llm"]["requests"] >= 3
    requests = app.gateways["claude"].provider.client.requests
    assert requests[[r["max_tokens"] for r in requests].index(0)]["tools"] is TOOLS, "the swarm pre-warms the shared prefix"
