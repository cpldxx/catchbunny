"""Deterministic replay and parallel delta debugging.

A finding is only as good as its reproduction. `replay` re-executes a recorded
trace in a fresh browser (resolving elements by signature, healing moved
controls). `ddmin` then shrinks a reproducing trace to a 1-minimal one: the
classic delta-debugging algorithm, except that every candidate of a round is
tested concurrently in its own browser context, so minimisation costs a few
rounds of wall-clock time instead of dozens of sequential replays.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from ..browser.observe import Observation
from ..browser.session import ActResult, Session, dead_click_signal
from ..domain import Action, Signal


@dataclass(slots=True)
class ReplayOutcome:
    completed: int = 0
    failed_at: int = -1
    error: str = ""
    healed: int = 0
    hit: bool = False
    signals: list[Signal] = field(default_factory=list)
    final: Observation | None = None


async def replay(
    session: Session,
    actions: Sequence[Action],
    *,
    until: Callable[[list[Signal]], bool] | None = None,
    before: Observation | None = None,
    on_step: Callable[[int, Action, ActResult], None] | None = None,
) -> ReplayOutcome:
    """Run `actions` in order, collecting every defect signal along the way.

    Stops early as soon as `until(signals)` holds - a verification replay does
    not need to finish the trace once the bug has shown itself.
    """
    out = ReplayOutcome()
    for i, action in enumerate(actions):
        res = await session.act(action, live=False)
        if on_step is not None:
            on_step(i, action, res)
        out.healed += res.healed
        obs = await session.observe()
        if before is not None and (dead := dead_click_signal(action, res, before, obs)) is not None:
            session.note(dead)
        out.signals += session.drain(i)
        out.final, before = obs, obs
        if until is not None and until(out.signals):
            out.hit, out.completed = True, i + 1
            return out
        if not res.ok:
            out.failed_at, out.error = i, res.error
            return out
        out.completed = i + 1
    out.hit = until(out.signals) if until is not None else False
    return out


Test = Callable[[list[Action]], Awaitable[bool]]


async def ddmin(actions: list[Action], test: Test, *, max_tests: int = 24) -> tuple[list[Action], int]:
    """Shrink `actions` to a subsequence that still makes `test` true.

    Returns (minimal_actions, tests_run). Each round tests all chunks and all
    complements at the current granularity in parallel; the budget caps the
    total number of replays, so the result is 1-minimal when the budget allows
    and simply "smaller" when it does not.
    """
    items = list(actions)
    cache: dict[tuple[int, ...], bool] = {}
    index = {id(a): i for i, a in enumerate(actions)}
    used = 0

    async def check(candidates: list[list[Action]]) -> list[bool]:
        nonlocal used
        keys = [tuple(index[id(a)] for a in c) for c in candidates]
        todo = [(k, c) for k, c in dict(zip(keys, candidates, strict=True)).items() if k not in cache][: max(0, max_tests - used)]
        used += len(todo)
        for (k, _), ok in zip(todo, await asyncio.gather(*(test(c) for _, c in todo)), strict=True):
            cache[k] = ok
        return [cache.get(k, False) for k in keys]

    n = 2
    while len(items) >= 2 and used < max_tests:
        size = math.ceil(len(items) / n)
        chunks = [items[i : i + size] for i in range(0, len(items), size)]
        complements = [[a for j, c in enumerate(chunks) if j != i for a in c] for i in range(len(chunks))]
        verdicts = await check(chunks + complements)
        chunk_ok, comp_ok = verdicts[: len(chunks)], verdicts[len(chunks) :]
        if any(chunk_ok):  # the bug lives entirely inside one chunk
            items, n = chunks[chunk_ok.index(True)], 2
        elif any(comp_ok):  # one chunk is irrelevant: drop it
            items, n = complements[comp_ok.index(True)], max(n - 1, 2)
        elif n >= len(items):
            break  # 1-minimal: removing any single action loses the bug
        else:
            n = min(len(items), n * 2)
    return items, used
