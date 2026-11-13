"""Anthropic provider: Claude as the testers' model.

Cost and latency design
-----------------------
* **One frozen prefix for the whole swarm.** `TOOLS` and `SYSTEM_BLOCKS` are
  module constants - byte-identical for every tester - with a cache breakpoint
  on the system block. Per-mission context lives in the first user message,
  after the breakpoint. N testers therefore share one cached prefix.
* **Pre-warm before fan-out.** A cache entry is readable only once the first
  response starts, so N testers launched together would all pay full price.
  `warm` writes the prefix once (`max_tokens=0`, no output billed) before the
  swarm starts; every tester's first turn is a cache read.
* **The transcript goes out verbatim.** History is never edited (that would
  both miss the cache and invalidate preserved thinking); observations are
  compact text outlines instead of screenshots, so a mission stays small
  without pruning. Top-level `cache_control` caches the growing conversation
  turn by turn.
"""

from __future__ import annotations

from typing import Any

import anthropic
from anthropic import AsyncAnthropic

from ..budget import Usage
from ..config import Settings, llm_credentials_present
from ..domain import Verdict
from .llm import SYSTEM, ModelError, Reply, ToolCall
from .llm import TOOLS as TOOL_SPECS

FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = ("claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")

# USD per million tokens: (input, output, cache read, 5-minute cache write).
# Snapshot of the published first-party rates (2026-11); unknown models cost 0
# and are flagged, rather than guessed.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10, 2.5),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-fable-5-2": (8.0, 40.0, 0.20, 10.0),
}

# The wire form of the shared prefix. Built once and never mutated: tools render
# first in the prompt, so any change here would invalidate the cache for every tester.
TOOLS: list[dict[str, Any]] = [{**spec, "strict": True} for spec in TOOL_SPECS]
SYSTEM_BLOCKS: list[dict[str, Any]] = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]


class AnthropicProvider:
    name = "claude"

    def __init__(self, settings: Settings, client: Any | None = None, model: str = "") -> None:
        self.s = settings
        self.model = model or settings.model
        self.planner_model = model or settings.planner_model
        self.concurrency = settings.llm_concurrency
        self.unpriced: set[str] = set()
        self._client = client
        self._injected = client is not None

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = AsyncAnthropic(max_retries=4, timeout=180.0)
        return self._client

    async def unavailable(self) -> str:
        if self._injected or llm_credentials_present():
            return ""
        return "No Anthropic credentials were found. Set ANTHROPIC_API_KEY, or choose another brain (heuristic, ollama)."

    async def close(self) -> None:
        if self._client is not None and not self._injected:
            await self._client.close()

    # ------------------------------------------------------------------ requests

    async def tester_turn(self, messages: list[dict[str, Any]]) -> Reply:
        return await self._send(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_BLOCKS,
            tools=TOOLS,
            messages=messages,
            cache_control={"type": "ephemeral"},  # caches the growing conversation each turn
            output_config={"effort": self.s.effort},
        )

    async def warm(self) -> Reply:
        """Write the shared tester prefix to the cache before the swarm fans out."""
        return await self._send(
            model=self.model,
            max_tokens=0,  # prefill only: writes the cache, generates nothing
            system=SYSTEM_BLOCKS,
            tools=TOOLS,
            output_config={"effort": self.s.effort},  # must match real traffic or the entry is never hit
            messages=[{"role": "user", "content": "warmup"}],
        )

    async def structured(self, system: str, prompt: str, schema: dict[str, Any]) -> Reply:
        reply = await self._send(
            model=self.planner_model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": self.s.planner_effort, "format": {"type": "json_schema", "schema": schema}},
        )
        reply.text = next((b.text for b in reply.content if b.type == "text"), "")
        return reply

    async def _send(self, **request: Any) -> Reply:
        try:
            # Opt in to server-side refusal fallbacks: a safety-classifier decline is
            # re-run on Anthropic's recommended fallback model inside the same call.
            if self.s.llm_fallbacks and request["model"].startswith(FALLBACK_MODELS):
                response = await self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **request)
            else:
                response = await self.client.messages.create(**request)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise ModelError(Verdict.BLOCKED, f"Model credentials were rejected: {exc.message}") from exc
        except anthropic.BadRequestError as exc:
            raise ModelError(Verdict.BLOCKED, f"The model request was invalid: {exc.message}") from exc
        except anthropic.RateLimitError as exc:
            raise ModelError(Verdict.INCONCLUSIVE, "Rate limited by the model API after retries.") from exc
        except anthropic.APIStatusError as exc:
            raise ModelError(Verdict.INCONCLUSIVE, f"Model API error {exc.status_code}.") from exc
        except anthropic.APIError as exc:
            raise ModelError(Verdict.INCONCLUSIVE, "Could not reach the model API.") from exc
        content = list(response.content or [])
        details = getattr(response, "stop_details", None)
        return Reply(
            content=content,
            calls=[ToolCall(id=b.id, name=b.name, input=dict(b.input)) for b in content if b.type == "tool_use"],
            text=" ".join(b.text for b in content if b.type == "text"),
            stop=response.stop_reason if response.stop_reason in ("refusal", "max_tokens", "tool_use") else "end",
            usage=self._price(request["model"], response.usage),
            detail=str(getattr(details, "category", "") or ""),
        )

    def _price(self, model: str, u: Any) -> Usage:
        read = getattr(u, "cache_read_input_tokens", 0) or 0
        write = getattr(u, "cache_creation_input_tokens", 0) or 0
        usage = Usage(requests=1, input_tokens=u.input_tokens or 0, output_tokens=u.output_tokens or 0, cache_read_tokens=read, cache_write_tokens=write)
        rates = next((p for name, p in PRICES.items() if model.startswith(name)), None)
        if rates is None:
            self.unpriced.add(model)
        else:
            usage.usd = (usage.input_tokens * rates[0] + usage.output_tokens * rates[1] + read * rates[2] + write * rates[3]) / 1e6
        return usage
