"""Run budget: one shared ledger for steps, wall-clock, browser time and model spend.

All mutation is synchronous. On a single-threaded event loop a method with no
`await` in it cannot be interleaved, so `take_step()` is an atomic
check-and-reserve without a lock: two testers can never both take the last step.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class Usage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    usd: float = 0.0

    def add(self, other: Usage) -> None:
        self.requests += other.requests
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.usd += other.usd

    def as_dict(self) -> dict[str, float | int]:
        total_in = self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
        return {
            "requests": self.requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_hit_rate": round(self.cache_read_tokens / total_in, 3) if total_in else 0.0,
            "usd": round(self.usd, 4),
        }


@dataclass(slots=True)
class Budget:
    max_steps: int
    max_seconds: float
    max_usd: float = 0.0
    steps: int = 0
    browser_seconds: float = 0.0
    llm: Usage = field(default_factory=Usage)
    started: float = field(default_factory=time.monotonic)
    _canceled: bool = False

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.max_seconds - self.elapsed)

    @property
    def stop_reason(self) -> str:
        if self._canceled:
            return "canceled"
        if self.steps >= self.max_steps:
            return "step budget exhausted"
        if self.elapsed >= self.max_seconds:
            return "time budget exhausted"
        if self.max_usd and self.llm.usd >= self.max_usd:
            return "model spend budget exhausted"
        return ""

    @property
    def exhausted(self) -> bool:
        return bool(self.stop_reason)

    def take_step(self) -> bool:
        if self.exhausted:
            return False
        self.steps += 1
        return True

    def cancel(self) -> None:
        self._canceled = True

    def as_dict(self) -> dict[str, object]:
        return {
            "steps": self.steps,
            "max_steps": self.max_steps,
            "seconds": round(self.elapsed, 2),
            "max_seconds": self.max_seconds,
            "browser_seconds": round(self.browser_seconds, 2),
            "llm": self.llm.as_dict(),
            "max_usd": self.max_usd,
        }
