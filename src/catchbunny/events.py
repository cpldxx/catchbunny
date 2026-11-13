"""In-process event bus with a durable log.

`publish` is synchronous and O(subscribers): a tester never awaits a slow
consumer. Each subscriber owns a bounded queue; when it falls behind, the oldest
event is dropped and the subscriber is told it lagged, so one stalled SSE
client cannot grow memory or slow the swarm. Every event is also appended to
SQLite, which is what makes `Last-Event-ID` resume and post-hoc replay work.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, Field

from .domain import now
from .store import Store


TERMINAL = "run.finished"  # the last event of every run; streams close after it


class Event(BaseModel):
    seq: int
    run_id: str
    type: str
    ts: float = Field(default_factory=now)
    data: dict[str, Any] = Field(default_factory=dict)


class Subscription:
    def __init__(self, bus: EventBus, run_id: str, size: int) -> None:
        self._bus = bus
        self.run_id = run_id
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=size)
        self.dropped = 0

    def offer(self, event: Event | None) -> None:
        if self._queue.full():
            self._queue.get_nowait()  # drop-oldest: freshest state wins
            self.dropped += 1
        self._queue.put_nowait(event)

    async def __aiter__(self) -> AsyncIterator[Event]:
        try:
            while (event := await self._queue.get()) is not None:
                yield event
        finally:
            self.close()

    def close(self) -> None:
        self._bus._subs[self.run_id].discard(self)


class EventBus:
    def __init__(self, store: Store | None = None, queue_size: int = 1024) -> None:
        self._store = store
        self._size = queue_size
        self._subs: dict[str, set[Subscription]] = defaultdict(set)
        self._seq = 0

    async def start(self) -> None:
        if self._store:
            self._seq = await self._store.max_event_seq()

    def publish(self, run_id: str, type: str, data: dict[str, Any] | None = None) -> Event:
        self._seq += 1
        event = Event(seq=self._seq, run_id=run_id, type=type, data=data or {})
        if self._store:
            self._store.append_event(event.seq, run_id, event.ts, type, event.data)
        for sub in tuple(self._subs.get(run_id, ())):
            sub.offer(event)
        return event

    def subscribe(self, run_id: str) -> Subscription:
        sub = Subscription(self, run_id, self._size)
        self._subs[run_id].add(sub)
        return sub

    def end(self, run_id: str) -> None:
        """Wake every subscriber of a finished run so their streams terminate."""
        for sub in tuple(self._subs.get(run_id, ())):
            sub.offer(None)

    async def stream(self, run_id: str, after: int = 0) -> AsyncIterator[Event]:
        """History then live, with no gap and no duplicate.

        Subscribe first so nothing published from now on is missed, wait for the
        writer to commit everything already published, replay from the log, then
        drain the live queue skipping whatever the replay already covered.
        """
        sub = self.subscribe(run_id)
        try:
            last = after
            if self._store:
                await self._store.flush()
                for row in await self._store.events_after(run_id, after):
                    last = row["seq"]
                    yield Event(**row)
                    if row["type"] == TERMINAL:
                        return  # the run already ended: history is the whole stream
            async for event in sub:
                if event.seq > last:
                    yield event
                    if event.type == TERMINAL:
                        return
        finally:
            sub.close()
