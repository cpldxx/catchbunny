"""Ollama provider: a local model as the testers' model. No key, no bill.

Speaks Ollama's native `/api/chat`, not its Anthropic- or OpenAI-compatible
endpoints: only the native API exposes what a swarm needs - the context size,
keep-alive, schema-constrained output (`format`) and the cache-hit counter.

A local model has a different cost model from a hosted one, so the context
strategy differs too:

* **Nothing is billed; the context window is the budget.** A page outline is
  the bulk of every turn and is stale as soon as the page changes. This
  provider therefore sends a *view* of the transcript in which only the newest
  outline is whole; each older one is cut to its URL and title. A mission's
  prompt stays a few thousand tokens however long it runs.
* **The masked outline is a prefix of the whole one**, so the runner's KV cache
  stays valid up to the point where the previous page began, and each turn
  re-evaluates one page, not the conversation.
* **One `num_ctx` for every request.** Ollama reloads a model whose context
  size changes (tens of seconds), so testers, planner and warm-up all send the
  same options.
* **`warm` loads the model** and evaluates the shared system prompt and tools
  once, so the swarm's first turns do not each wait on a cold start.

Small models are less reliable callers than Claude: arguments arrive with wrong
types, or as prose. That is handled upstream in `ModelBrain` (the correction
loop), not hidden here.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..budget import Usage
from ..config import Settings
from ..domain import Verdict, new_id
from .llm import PAGE_MARK, SYSTEM, TOOLS, ModelError, Reply, ToolCall

_MASKED = "(older outline omitted: the page has changed since)"

# Ollama's function-calling shape for the same tools every provider gets.
_TOOLS: list[dict[str, Any]] = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in TOOLS]


def _mask(text: str) -> str:
    head, mark, outline = text.partition(PAGE_MARK)
    if not mark:
        return text
    return head + mark + "\n".join(outline.split("\n", 2)[:2]) + "\n" + _MASKED


def to_wire(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The transcript as Ollama messages, with every page outline but the newest masked."""

    def texts(m: dict[str, Any]) -> list[str]:
        return [b["text"] for b in m["content"] if b.get("type") == "text"]

    pages = [i for i, m in enumerate(messages) if m["role"] == "user" and any(PAGE_MARK in t for t in texts(m))]
    replies = [i for i, m in enumerate(messages) if m["role"] == "assistant"]
    newest_page, newest_reply = (pages or [-1])[-1], (replies or [-1])[-1]
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    names: dict[str, str] = {}
    for i, m in enumerate(messages):
        blocks = m["content"]
        if m["role"] == "assistant":
            calls = [b for b in blocks if b["type"] == "tool_use"]
            names.update({b["id"]: b["name"] for b in calls})
            msg: dict[str, Any] = {"role": "assistant", "content": "".join(b["text"] for b in blocks if b["type"] == "text")}
            thinking = "".join(b["thinking"] for b in blocks if b["type"] == "thinking")
            if thinking and i == newest_reply:  # reasoning about a page that is gone is dead weight
                msg["thinking"] = thinking
            if calls:
                msg["tool_calls"] = [{"type": "function", "function": {"index": n, "name": b["name"], "arguments": b["input"]}} for n, b in enumerate(calls)]
            out.append(msg)
            continue
        for b in blocks:
            if b["type"] == "tool_result":
                out.append({"role": "tool", "tool_name": names.get(b["tool_use_id"], ""), "content": ("ERROR: " if b.get("is_error") else "") + str(b["content"])})
        text = "\n".join(texts(m))
        if text:
            out.append({"role": "user", "content": text if i >= newest_page else _mask(text)})
    return out


class OllamaProvider:
    name = "ollama"

    def __init__(self, settings: Settings, model: str = "", http: httpx.AsyncClient | None = None) -> None:
        self.s = settings
        self.model = model or settings.ollama_model
        self.concurrency = settings.ollama_concurrency
        self.http = http or httpx.AsyncClient(base_url=settings.ollama_host, timeout=httpx.Timeout(settings.ollama_timeout_s, connect=5.0))
        self._thinks = False  # learned from the server in `unavailable`

    async def close(self) -> None:
        await self.http.aclose()

    # ------------------------------------------------------------------ discovery

    async def catalog(self) -> list[dict[str, Any]]:
        """Installed models that can call tools, as the server reports them. Raises httpx.HTTPError when unreachable."""
        response = await self.http.get("/api/tags", timeout=5.0)
        response.raise_for_status()
        return [
            {
                "name": m["name"],
                "parameters": m.get("details", {}).get("parameter_size", ""),
                "size_gb": round(m.get("size", 0) / 1e9, 1),
                "thinking": "thinking" in m.get("capabilities", []),
            }
            # Servers too old to report capabilities list every model; `unavailable` asks about the chosen one.
            for m in response.json().get("models", [])
            if "tools" in m.get("capabilities", ["tools"])
        ]

    async def installed(self) -> tuple[list[dict[str, Any]], str]:
        """(tool-capable models, "") or ([], why there are none) - for a run form to offer."""
        try:
            models = await self.catalog()
        except (httpx.HTTPError, ValueError, KeyError):
            return [], f"Ollama is not reachable at {str(self.http.base_url).rstrip('/')}."
        return models, "" if models else "No installed Ollama model can call tools."

    async def unavailable(self) -> str:
        host = str(self.http.base_url).rstrip("/")
        try:
            usable = [m["name"] for m in await self.catalog()]
            hint = f" Installed models that can call tools: {', '.join(usable)}." if usable else " No installed model can call tools; try `ollama pull qwen3`."
            if not self.model:
                return f"No Ollama model was chosen. Pass a model (--model, or CATCHBUNNY_OLLAMA_MODEL).{hint}"
            shown = await self.http.post("/api/show", json={"model": self.model}, timeout=10.0)
        except (httpx.HTTPError, ValueError, KeyError):
            return f"Ollama is not reachable at {host}. Start it with `ollama serve`, or set OLLAMA_HOST."
        if shown.status_code == 404:
            return f"Ollama model {self.model!r} is not installed (`ollama pull {self.model}`).{hint}"
        capabilities = shown.json().get("capabilities")
        if capabilities is not None and "tools" not in capabilities:
            return f"Ollama model {self.model!r} cannot call tools, which a tester needs.{hint}"
        self._thinks = "thinking" in (capabilities or [])
        return ""

    # ------------------------------------------------------------------ requests

    async def tester_turn(self, messages: list[dict[str, Any]]) -> Reply:
        return await self._chat(to_wire(SYSTEM, messages), tools=_TOOLS)

    async def warm(self) -> Reply:
        return await self._chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": "warmup"}], tools=_TOOLS, num_predict=1)

    async def structured(self, system: str, prompt: str, schema: dict[str, Any]) -> Reply:
        return await self._chat([{"role": "system", "content": system}, {"role": "user", "content": prompt}], format=schema)

    async def _chat(self, messages: list[dict[str, Any]], *, num_predict: int | None = None, **extra: Any) -> Reply:
        s = self.s
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": s.ollama_keep_alive,
            # Identical for every request to this model: a different num_ctx forces a reload.
            "options": {"num_ctx": s.ollama_num_ctx, "temperature": s.ollama_temperature, "num_predict": num_predict or s.ollama_max_output},
            **extra,
        }
        if self._thinks:
            body["think"] = {"off": False, "on": True}.get(s.ollama_think, s.ollama_think)
        try:
            response = await self.http.post("/api/chat", json=body)
        except httpx.TimeoutException as exc:
            raise ModelError(Verdict.INCONCLUSIVE, f"Ollama did not answer within {s.ollama_timeout_s:.0f}s.") from exc
        except httpx.HTTPError as exc:
            raise ModelError(Verdict.INCONCLUSIVE, f"Could not reach Ollama at {str(self.http.base_url).rstrip('/')}.") from exc
        if response.status_code >= 400:
            try:
                reason = str(response.json().get("error", ""))[:200]
            except ValueError:
                reason = response.text[:200]
            verdict = Verdict.BLOCKED if response.status_code < 500 else Verdict.INCONCLUSIVE  # 4xx: asking again will not help
            raise ModelError(verdict, f"Ollama error {response.status_code}: {reason}")
        return self._reply(response.json())

    @staticmethod
    def _reply(data: dict[str, Any]) -> Reply:
        message = data.get("message") or {}
        calls: list[ToolCall] = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):  # some templates hand the arguments back as a JSON string
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            calls.append(ToolCall(id=call.get("id") or new_id("call"), name=str(fn.get("name", "")), input=args if isinstance(args, dict) else {"_raw": args}))
        text, thinking = message.get("content") or "", message.get("thinking") or ""
        content: list[dict[str, Any]] = [{"type": "thinking", "thinking": thinking}] if thinking else []
        if text or not calls:
            content.append({"type": "text", "text": text or "(no output)"})
        content += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls]
        cached = int(data.get("prompt_eval_cached_count") or 0)
        usage = Usage(
            requests=1,
            input_tokens=max(0, int(data.get("prompt_eval_count") or 0) - cached),  # evaluated this turn
            output_tokens=int(data.get("eval_count") or 0),
            cache_read_tokens=cached,  # reused from the runner's KV cache
        )
        stop = "tool_use" if calls else "max_tokens" if data.get("done_reason") == "length" else "end"
        return Reply(content=content, calls=calls, text=text, stop=stop, usage=usage)
