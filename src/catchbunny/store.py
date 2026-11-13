"""Persistence: a single-writer SQLite document store and a content-addressed
artifact store.

Why single-writer: SQLite allows one writer at a time. Instead of letting N
testers contend for the write lock, every write goes onto one queue and one
task drains it, committing a whole batch per transaction (group commit) in a
single thread hop. Hot-path writes (`put`, `append_event`) are synchronous
enqueues that never block a tester; `flush()` is the durability barrier.
Reads use a separate connection, which WAL mode lets run alongside the writer.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
  kind TEXT NOT NULL, id TEXT NOT NULL, parent TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL, data TEXT NOT NULL, PRIMARY KEY (kind, id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS docs_parent ON docs (kind, parent, created);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY, run_id TEXT NOT NULL, ts REAL NOT NULL,
  type TEXT NOT NULL, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run ON events (run_id, seq);
CREATE TABLE IF NOT EXISTS idempotency (key TEXT PRIMARY KEY, run_id TEXT NOT NULL);
"""

_PUT = "INSERT INTO docs (kind, id, parent, created, data) VALUES (?,?,?,?,?) ON CONFLICT(kind, id) DO UPDATE SET data=excluded.data, parent=excluded.parent"
_EVENT = "INSERT OR IGNORE INTO events (seq, run_id, ts, type, data) VALUES (?,?,?,?,?)"

_Write = tuple[str, tuple[Any, ...], asyncio.Future[None] | None]


def _dump(obj: BaseModel | dict[str, Any]) -> str:
    return obj.model_dump_json() if isinstance(obj, BaseModel) else json.dumps(obj, separators=(",", ":"))


class Store:
    BATCH = 512

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._queue: asyncio.Queue[_Write] = asyncio.Queue()
        self._writer: asyncio.Task[None] | None = None
        self._wconn: sqlite3.Connection | None = None
        self._rconn: sqlite3.Connection | None = None
        self._rlock = threading.Lock()
        self._wlock = threading.Lock()
        self.batches = 0
        self.writes = 0

    # ------------------------------------------------------------------ lifecycle

    async def open(self) -> None:
        if self.path == ":memory:":
            # An in-memory database exists per connection, so tests share one
            # connection and serialise on one lock instead of using WAL.
            self._wconn = self._rconn = sqlite3.connect(":memory:", check_same_thread=False)
            self._rlock = self._wlock
            self._wconn.executescript(SCHEMA)
        else:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._wconn = sqlite3.connect(self.path, check_same_thread=False)
            self._wconn.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;" + SCHEMA)
            self._rconn = sqlite3.connect(self.path, check_same_thread=False)  # WAL: reads never wait for the writer
        self._writer = asyncio.create_task(self._drain(), name="store-writer")

    async def close(self) -> None:
        await self.flush()
        if self._writer:
            self._writer.cancel()
            await asyncio.gather(self._writer, return_exceptions=True)
        for conn in {self._wconn, self._rconn}:
            if conn:
                conn.close()

    # ------------------------------------------------------------------ write path

    async def _drain(self) -> None:
        while True:
            batch = [await self._queue.get()]
            while len(batch) < self.BATCH and not self._queue.empty():
                batch.append(self._queue.get_nowait())
            error: Exception | None = None
            try:
                await asyncio.to_thread(self._apply, batch)
            except Exception as exc:  # surface to awaiting callers, keep the writer alive
                error = exc
            for _, _, fut in batch:
                if fut and not fut.done():
                    fut.set_exception(error) if error else fut.set_result(None)
                self._queue.task_done()
            self.batches += 1
            self.writes += len(batch)

    def _apply(self, batch: list[_Write]) -> None:
        assert self._wconn is not None
        with self._wlock:
            try:
                for sql, params, _ in batch:
                    self._wconn.execute(sql, params)
                self._wconn.commit()
            except Exception:
                self._wconn.rollback()
                raise

    def _enqueue(self, sql: str, params: tuple[Any, ...], wait: bool = False) -> asyncio.Future[None] | None:
        fut = asyncio.get_running_loop().create_future() if wait else None
        self._queue.put_nowait((sql, params, fut))
        return fut

    def put(self, kind: str, id: str, obj: BaseModel | dict[str, Any], parent: str = "", created: float = 0.0) -> None:
        """Write-behind upsert: returns immediately, durable after the next batch."""
        self._enqueue(_PUT, (kind, id, parent, created, _dump(obj)))

    async def put_now(self, kind: str, id: str, obj: BaseModel | dict[str, Any], parent: str = "", created: float = 0.0) -> None:
        fut = self._enqueue(_PUT, (kind, id, parent, created, _dump(obj)), wait=True)
        assert fut is not None
        await fut

    def append_event(self, seq: int, run_id: str, ts: float, type: str, data: dict[str, Any]) -> None:
        self._enqueue(_EVENT, (seq, run_id, ts, type, json.dumps(data, separators=(",", ":"), default=str)))

    async def delete(self, kind: str, id: str) -> None:
        fut = self._enqueue("DELETE FROM docs WHERE kind=? AND id=?", (kind, id), wait=True)
        assert fut is not None
        await fut

    async def flush(self) -> None:
        """Barrier: returns once everything enqueued so far is committed."""
        await self._queue.join()

    # ------------------------------------------------------------------ read path

    def _query(self, sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        assert self._rconn is not None
        with self._rlock:
            return self._rconn.execute(sql, params).fetchall()

    async def get(self, kind: str, id: str) -> dict[str, Any] | None:
        rows = await asyncio.to_thread(self._query, "SELECT data FROM docs WHERE kind=? AND id=?", (kind, id))
        return json.loads(rows[0][0]) if rows else None

    async def list(self, kind: str, parent: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        if parent is None:
            rows = await asyncio.to_thread(self._query, "SELECT data FROM docs WHERE kind=? ORDER BY created LIMIT ?", (kind, limit))
        else:
            rows = await asyncio.to_thread(
                self._query, "SELECT data FROM docs WHERE kind=? AND parent=? ORDER BY created LIMIT ?", (kind, parent, limit)
            )
        return [json.loads(r[0]) for r in rows]

    async def events_after(self, run_id: str, seq: int, limit: int = 5000) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(
            self._query, "SELECT seq, run_id, ts, type, data FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?", (run_id, seq, limit)
        )
        return [{"seq": r[0], "run_id": r[1], "ts": r[2], "type": r[3], "data": json.loads(r[4])} for r in rows]

    async def max_event_seq(self) -> int:
        rows = await asyncio.to_thread(self._query, "SELECT COALESCE(MAX(seq), 0) FROM events", ())
        return int(rows[0][0])

    async def idempotent_run(self, key: str, run_id: str) -> str:
        """Atomically bind `key` to `run_id`; returns whichever run owns the key."""
        fut = self._enqueue("INSERT OR IGNORE INTO idempotency (key, run_id) VALUES (?,?)", (key, run_id), wait=True)
        assert fut is not None
        await fut
        rows = await asyncio.to_thread(self._query, "SELECT run_id FROM idempotency WHERE key=?", (key,))
        return str(rows[0][0])


class ArtifactStore:
    """Content-addressed blobs: identical screenshots are stored once."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    async def put(self, data: bytes, ext: str) -> str:
        name = f"{hashlib.sha256(data).hexdigest()[:24]}.{ext}"
        path = self.root / name
        if not path.exists():
            await asyncio.to_thread(path.write_bytes, data)
        return name

    async def put_file(self, source: Path | str, ext: str) -> str:
        """Adopt a file a browser wrote (video, trace); returns '' if it does not exist."""
        source = Path(source)
        if not source.is_file():
            return ""
        return await self.put(await asyncio.to_thread(source.read_bytes), ext)

    def path(self, name: str) -> Path | None:
        if "/" in name or "\\" in name or name.startswith("."):
            return None
        path = self.root / name
        return path if path.is_file() else None
