"""The Ollama adapter, driven by a fake Ollama server (httpx.MockTransport): no
daemon, no model. The fake answers `/api/tags`, `/api/show` and `/api/chat` the
way a real server does, so these tests pin the wire contract and the context
strategy that makes a local model usable for long missions."""

from __future__ import annotations

import json
from typing import Any

import httpx

from catchbunny.brain.base import Brief, Feedback, IntentResult
from catchbunny.brain.llm import PAGE_MARK, TOOLS, LLMGateway, ModelBrain
from catchbunny.brain.ollama import OllamaProvider
from catchbunny.browser.observe import Observation
from catchbunny.budget import Budget
from catchbunny.config import Settings
from catchbunny.domain import Mission, Policy, RunSpec, RunStatus, Verdict
from catchbunny.engine.orchestrator import App

MODELS = [
    {"name": "qwen3:8b", "size": 5_200_000_000, "details": {"parameter_size": "8.2B"}, "capabilities": ["completion", "tools", "thinking"]},
    {"name": "embed:latest", "size": 1_000_000_000, "details": {"parameter_size": "475M"}, "capabilities": ["embedding"]},
]


def call(tool: str, /, **arguments: Any) -> dict[str, Any]:
    return {"id": f"call_{tool}", "function": {"name": tool, "arguments": arguments}}


def answer(*calls: dict[str, Any], content: str = "", prompt: int = 1200, cached: int = 1000, **extra: Any) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content, **extra}
    if calls:
        message["tool_calls"] = list(calls)
    return {"message": message, "done": True, "done_reason": "stop", "prompt_eval_count": prompt, "prompt_eval_cached_count": cached, "eval_count": 30}


class FakeOllama:
    def __init__(self, script: Any, models: list[dict[str, Any]] | None = None) -> None:
        self.script, self.models, self.chats = script, MODELS if models is None else models, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(200, json={"models": self.models})
        body = json.loads(request.content)
        if path == "/api/show":
            found = next((m for m in self.models if m["name"] == body["model"]), None)
            return httpx.Response(200, json={"capabilities": found["capabilities"]}) if found else httpx.Response(404, json={"error": "model not found"})
        self.chats.append(body)
        result = self.script(body)
        return result if isinstance(result, httpx.Response) else httpx.Response(200, json=result)

    def provider(self, settings: Settings | None = None, model: str = "qwen3:8b") -> OllamaProvider:
        http = httpx.AsyncClient(base_url="http://ollama.test", transport=httpx.MockTransport(self))
        return OllamaProvider(settings or Settings(), model=model, http=http)


def _brief() -> Brief:
    return Brief(mission=Mission(title="Probe the cart", charter="Check quantities."), base_url="http://app.test", policy=Policy.SAFE_WRITES, persona={"name": "visitor"})


def _page(path: str, button: str) -> Observation:
    raw = {"url": f"http://app.test{path}", "title": path, "text": f"long body of {path} " * 20,
           "elements": [{"ref": "e1", "role": "button", "name": button, "tag": "button", "attrs": {}, "text": button, "near": "", "css": "button"}]}  # fmt: skip
    return Observation.from_raw(raw)


async def test_wire_contract_and_only_the_newest_page_is_sent_whole() -> None:
    turns = iter(
        [
            answer(call("click", ref="e1", why="open it"), call("assert_check", kind="url_contains", value="/a", role="", name=""), thinking="plan A"),
            answer(call("click", ref="e1", why="next"), thinking="plan B"),
            answer(call("finish", verdict="passed", summary="ok")),
        ]
    )
    server = FakeOllama(lambda _: next(turns))
    provider = server.provider()
    assert await provider.unavailable() == ""  # also learns that this model can think
    budget = Budget(max_steps=10, max_seconds=60)
    brain = ModelBrain(_brief(), LLMGateway(Settings(), provider), budget)

    first = await brain.decide(_page("/a", "Alpha"), Feedback())
    assert [i.kind for i in first] == ["act", "check"] and brain.name == "ollama"
    results = [IntentResult(id=first[0].id, ok=True, message="clicked"), IntentResult(id=first[1].id, ok=False, message="FAIL url_contains")]
    second = await brain.decide(_page("/b", "Beta"), Feedback(results=results))
    done = await brain.decide(_page("/c", "Gamma"), Feedback(results=[IntentResult(id=second[0].id, ok=True, message="clicked")]))
    assert done[0].kind == "finish" and done[0].verdict is Verdict.PASSED

    one, two, three = server.chats
    assert one["model"] == "qwen3:8b" and one["stream"] is False and one["think"] is False and one["keep_alive"] == "10m"
    assert len({json.dumps(c["options"], sort_keys=True) for c in server.chats}) == 1, "identical options on every request: a new num_ctx reloads the model"
    assert [t["function"]["name"] for t in one["tools"]] == [t["name"] for t in TOOLS] and one["tools"][0]["function"]["parameters"] == TOOLS[0]["input_schema"]
    assert [m["role"] for m in two["messages"]] == ["system", "user", "assistant", "tool", "tool", "user"]
    assistant = two["messages"][2]
    assert [c["function"]["name"] for c in assistant["tool_calls"]] == ["click", "assert_check"] and assistant["thinking"] == "plan A"
    assert [(m["tool_name"], m["content"]) for m in two["messages"][3:5]] == [("click", "clicked"), ("assert_check", "ERROR: FAIL url_contains")]

    def outlines(chat: dict[str, Any]) -> list[str]:
        return [m["content"].split(PAGE_MARK, 1)[1] for m in chat["messages"] if m["role"] == "user" and PAGE_MARK in m["content"]]

    old_a, old_b, new_c = outlines(three)
    assert 'button "Gamma"' in new_c and "long body" in new_c, "the page being acted on is whole"
    assert old_a.startswith("URL: http://app.test/a\nTITLE: /a\n(older outline omitted") and "Alpha" not in old_a and "Beta" not in old_b
    whole_a = outlines(one)[0]
    assert whole_a.startswith(old_a.rsplit("\n", 1)[0]), "a masked outline is a prefix of the whole one, so the KV cache survives up to it"
    assert "MISSION: Probe the cart" in three["messages"][1]["content"], "the brief is never masked"
    assert "thinking" not in three["messages"][2] and three["messages"][-3]["thinking"] == "plan B", "only the latest reasoning is replayed"
    assert sum(len(m["content"]) for m in three["messages"][1:]) < sum(len(m["content"]) for m in one["messages"][1:]) * 2.2, "the prompt does not grow by a page per turn"
    # Local usage: nothing is billed; cached tokens are the KV-cache prefix the runner reused.
    assert budget.llm.requests == 3 and budget.llm.usd == 0 and budget.llm.cache_read_tokens == 3000 and budget.llm.input_tokens == 600


async def test_a_model_that_answers_in_prose_or_with_bad_arguments_is_corrected() -> None:
    turns = iter(
        [
            answer(content="I will click the button now."),  # prose, no tool call
            answer(call("click", reference="e1")),  # wrong argument name
            answer({"id": "c3", "function": {"name": "click", "arguments": '{"ref": "e1", "why": "third time"}'}}),  # arguments as a JSON string
        ]
    )
    server = FakeOllama(lambda _: next(turns))
    brain = ModelBrain(_brief(), LLMGateway(Settings(), server.provider()), Budget(max_steps=10, max_seconds=60))
    (intent,) = await brain.decide(_page("/a", "Alpha"), Feedback())
    assert intent.kind == "act" and intent.action.target.ref == "e1"  # type: ignore[union-attr]
    second, third = server.chats[1]["messages"], server.chats[2]["messages"]
    assert second[-1]["role"] == "user" and "did not call a tool" in second[-1]["content"]
    assert third[-2] == {"role": "tool", "tool_name": "click", "content": "ERROR: Invalid tool input: 'ref'"} and "valid input" in third[-1]["content"]
    assert PAGE_MARK + "URL: http://app.test/a\nTITLE: /a\n[e1]" in third[1]["content"], "a correction is not a new page: the outline stays whole"

    stuck = ModelBrain(_brief(), LLMGateway(Settings(), FakeOllama(lambda _: answer(content="Sure!")).provider()), Budget(max_steps=10, max_seconds=60))
    (stop,) = await stuck.decide(_page("/a", "Alpha"), Feedback())
    assert stop.kind == "finish" and stop.verdict is Verdict.INCONCLUSIVE and "usable tool call" in stop.summary


async def test_setup_problems_are_reported_in_words_before_anything_runs(app: App, fixed_shop: str) -> None:
    server = FakeOllama(lambda _: httpx.Response(500, json={"error": "llama runner process has terminated"}))
    assert "not installed" in await server.provider(model="llama9:1b").unavailable() and "qwen3:8b" in await server.provider(model="llama9:1b").unavailable()
    assert "cannot call tools" in await server.provider(model="embed:latest").unavailable()
    assert "No Ollama model was chosen" in await server.provider(model="").unavailable()
    assert await server.provider().installed() == ([{"name": "qwen3:8b", "parameters": "8.2B", "size_gb": 5.2, "thinking": True}], "")

    def refuse(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    down = OllamaProvider(Settings(), model="qwen3:8b", http=httpx.AsyncClient(base_url="http://ollama.test", transport=httpx.MockTransport(refuse)))
    assert "not reachable at http://ollama.test" in await down.unavailable() and (await down.installed())[0] == []

    # A server error mid-mission ends that mission as inconclusive; it never looks like a pass.
    brain = ModelBrain(_brief(), LLMGateway(Settings(), server.provider()), Budget(max_steps=5, max_seconds=5))
    (stop,) = await brain.decide(_page("/a", "Alpha"), Feedback())
    assert stop.verdict is Verdict.INCONCLUSIVE and "Ollama error 500: llama runner" in stop.summary

    # And a run that asks for a missing model is blocked with the fix, before a browser opens.
    from conftest import shop_project

    app.gateways["ollama:llama9:1b"] = LLMGateway(app.settings, server.provider(app.settings, model="llama9:1b"))
    run = await app.runs.wait((await app.runs.start(shop_project(fixed_shop), RunSpec(brain="ollama", model="llama9:1b", testers=1, max_steps=5))).id)
    assert run.status is RunStatus.BLOCKED and "ollama pull llama9:1b" in run.error and await app.store.list("mission", parent=run.id) == []


async def test_planner_uses_schema_constrained_output_and_warmup_loads_the_model() -> None:
    plan = {"charters": [{"title": "t", "charter": "c", "persona": "visitor", "start_path": "/"}]}
    server = FakeOllama(lambda body: answer(content=json.dumps(plan)) if "format" in body else answer(content="", prompt=1500, cached=0))
    gateway = LLMGateway(Settings(), server.provider())
    schema = {"type": "object", "properties": {"charters": {"type": "array"}}, "required": ["charters"]}
    assert await gateway.structured(system="Plan.", prompt="Two charters.", schema=schema) == plan
    assert await gateway.prewarm()
    planned, warm = server.chats
    assert planned["format"] == schema and "tools" not in planned and [m["role"] for m in planned["messages"]] == ["system", "user"]
    assert warm["options"]["num_predict"] == 1 and warm["options"]["num_ctx"] == planned["options"]["num_ctx"] and len(warm["tools"]) == len(TOOLS)
    assert gateway.total.requests == 2 and gateway.total.usd == 0
