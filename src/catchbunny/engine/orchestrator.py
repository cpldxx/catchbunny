"""Run lifecycle: prepare -> plan -> swarm -> triage -> report.

`App` is the composition root (store, event bus, browser pool, model gateway).
`RunManager` owns runs: admission control, cancellation, and the three
pipelines (discovery, deep scan, scenarios).

Scheduling
----------
* A run is one asyncio task; its testers are workers in a TaskGroup pulling
  missions from a queue, so `testers` is a hard concurrency cap and an idle
  worker immediately takes the next mission.
* Missions catch their own failures - one broken page ends one mission, not
  the run - while cancellation propagates structurally through the group.
* Every tester draws on one `Budget` (steps, wall clock, model spend), and all
  runs share one `BrowserPool`, so neither a run nor the process can
  over-commit the machine.
* Sign-in is single-flight per persona: the first tester to need it logs in
  once, everyone else starts from a copy of that session.
* That copy means testers of one persona share the account's server-side state
  (its cart, its records). Exploration tolerates that; anything that asserts on
  state - verification replays, minimisation, scenarios - takes the persona's
  account lock, so two replays never rearrange the same account under each
  other. Anonymous personas have no shared state and stay fully parallel.
* What an account keeps on the server survives a fresh sign-in. When the app
  has an endpoint that puts its test data back (`Environment.reset_path`), it
  is called before every such replay, and those replays then run one at a time:
  a reset is the whole app's state, not one account's.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any

from ..brain.base import Brain, Brief
from ..brain.claude import AnthropicProvider
from ..brain.llm import LLMGateway, ModelBrain, plan_charters
from ..brain.ollama import OllamaProvider
from ..brain.heuristic import HeuristicBrain
from ..browser import netguard
from ..browser.session import BrowserPool, EngineUnavailable, Session
from ..budget import Budget
from ..config import Settings
from ..domain import (
    Action,
    Check,
    Environment,
    Finding,
    FindingStatus,
    LearnedStep,
    Mission,
    Persona,
    Project,
    Run,
    RunKind,
    RunSpec,
    RunStatus,
    Scenario,
    Severity,
    Verdict,
    now,
    origin_of,
    path_pattern,
    step_hash,
    url_path,
)
from ..events import EventBus
from ..store import ArtifactStore, Store
from ..vault import Vault
from .productmap import ProductMap
from .replay import replay
from .scenarios import ScenarioReport, ScenarioRunner
from .tester import MissionResult, Tester
from .triage import Triage, rank, summarize

_SEVERITY_RANK = {Severity.P0: 0, Severity.P1: 1, Severity.P2: 2, Severity.P3: 3}


class RunBlocked(Exception):
    """The run could not test anything; the message says why and what to fix."""


class App:
    """Composition root. One per process; everything else receives it."""

    def __init__(self, settings: Settings | None = None, *, in_memory: bool = False) -> None:
        self.settings = settings or Settings()
        self.store = Store(":memory:" if in_memory else self.settings.db_path)
        self.bus = EventBus(self.store)
        self.artifacts = ArtifactStore(self.settings.artifact_dir)
        self.vault = Vault(self.settings.data_dir)
        self.pool = BrowserPool(self.settings)
        self.gateways: dict[str, LLMGateway] = {}  # one per provider and model: each owns a semaphore and a usage ledger
        self.runs = RunManager(self)

    async def start(self) -> None:
        await self.store.open()
        await self.bus.start()
        await self.runs.recover()
        await self.pool.start()

    async def stop(self) -> None:
        await self.runs.shutdown()
        await self.pool.stop()
        for gateway in self.gateways.values():
            await gateway.close()
        await self.store.close()

    def gateway(self, provider: str, model: str = "") -> LLMGateway:
        """The gateway for a model provider ("claude", "ollama"), created on first use."""
        key = f"{provider}:{model}" if model else provider
        if key not in self.gateways:
            adapter = OllamaProvider(self.settings, model=model) if provider == "ollama" else AnthropicProvider(self.settings, model=model)
            self.gateways[key] = LLMGateway(self.settings, adapter)
        return self.gateways[key]

    # Every read and write of a project goes through these two, so a password is
    # ciphertext everywhere on disk and plaintext only in the memory of a run.

    async def save_project(self, project: Project) -> None:
        doc = project.model_dump(mode="json")
        for persona in doc["personas"]:
            if persona.get("credential"):
                persona["credential"]["password"] = self.vault.seal(persona["credential"]["password"])
        await self.store.put_now("project", project.id, doc, created=project.created_at)

    def load_project(self, doc: dict[str, Any]) -> Project:
        project = Project(**doc)
        for persona in project.personas:
            if persona.credential:
                persona.credential.password = self.vault.open(persona.credential.password)
        return project

    async def __aenter__(self) -> App:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()


@dataclass(slots=True)
class RunContext:
    run: Run
    project: Project
    env: Environment
    pmap: ProductMap
    budget: Budget
    gateway: LLMGateway | None  # None: a model-free run
    bus: EventBus
    storage: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    login_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    account_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    blocked: dict[str, str] = field(default_factory=dict)
    warned: set[str] = field(default_factory=set)  # warnings already given once
    replays: int = 0
    hunt_seconds: float = 0.0  # wall clock spent testing, excluding triage

    def emit(self, type: str, data: dict[str, Any] | None = None) -> None:
        self.bus.publish(self.run.id, type, data)

    def warn(self, message: str) -> None:
        """Say something the reader of the run should know, once."""
        if message not in self.warned:
            self.warned.add(message)
            self.emit("run.warning", {"message": message})

    def personas(self) -> list[Persona]:
        wanted = self.run.spec.personas
        return [p for p in self.project.personas if not wanted or p.name in wanted] or self.project.personas[:1]


def _alive(pid: int) -> bool:
    """Whether another process that started a run is still there. Our own pid is not 'another': at startup, a run carrying it is left over from a process that died."""
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # it exists but is not ours to signal
        return True
    return True


class RunManager:
    def __init__(self, app: App) -> None:
        self.app = app
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._budgets: dict[str, Budget] = {}
        self._admission = asyncio.Semaphore(app.settings.max_concurrent_runs)

    # ------------------------------------------------------------------ public API

    async def start(self, project: Project, spec: RunSpec, idempotency_key: str = "") -> Run:
        run = Run(project_id=project.id, spec=spec, pid=os.getpid())
        if idempotency_key:  # a retried request returns the original run instead of starting a second one
            owner = await self.app.store.idempotent_run(f"{project.id}:{idempotency_key}", run.id)
            if owner != run.id:
                existing = await self.app.store.get("run", owner)
                if existing:
                    return Run(**existing)
        await self.app.store.put_now("run", run.id, run, parent=project.id, created=run.created_at)
        task = asyncio.create_task(self._execute(run, project), name=f"run-{run.id}")
        self._tasks[run.id] = task
        task.add_done_callback(lambda _: self._tasks.pop(run.id, None))
        return run

    async def cancel(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        if task is None:
            return False
        if budget := self._budgets.get(run_id):
            budget.cancel()  # testers stop at their next step...
        task.cancel()  # ...and anything awaiting is interrupted now
        return True

    async def wait(self, run_id: str, timeout: float | None = None) -> Run:
        """The run once it has finished - or, with a timeout, as it stands when the time is up.

        Giving up on the wait does not stop the run: a caller with a deadline of its own
        (an agent's tool call) comes back for the rest.
        """
        if task := self._tasks.get(run_id):
            await asyncio.wait({task}, timeout=timeout)
            await self.app.store.flush()
        else:  # finished long ago, or it belongs to another process sharing this data directory: watch the record
            deadline = None if timeout is None else time.monotonic() + timeout
            while (doc := await self.app.store.get("run", run_id)) and doc["status"] in (RunStatus.QUEUED, RunStatus.RUNNING) and _alive(doc.get("pid", 0)):
                if deadline is not None and time.monotonic() >= deadline:
                    break
                await asyncio.sleep(0.5)
        data = await self.app.store.get("run", run_id)
        if data is None:
            raise KeyError(run_id)
        run = Run(**data)
        if run.status in (RunStatus.QUEUED, RunStatus.RUNNING) and run_id not in self._tasks and not _alive(run.pid):
            self._close_out(run)  # its process died without finishing it
            await self.app.store.flush()
        return run

    def _close_out(self, run: Run) -> None:
        run.status, run.phase, run.finished_at = RunStatus.ERROR, "finished", now()
        run.error = "Interrupted: the process stopped while this run was in progress."
        self._save(run)
        self.app.bus.publish(run.id, "run.finished", {"run": run.model_dump(mode="json")})

    def progress(self, run_id: str) -> dict[str, Any]:
        """How far a run of this process has got. Empty for a run this process does not own."""
        budget = self._budgets.get(run_id)
        return {"steps": budget.steps, "max_steps": budget.max_steps, "seconds": round(budget.elapsed, 1)} if budget and run_id in self._tasks else {}

    async def recover(self) -> int:
        """Runs left 'running' by a process that died are closed out, not left to hang forever."""
        stale = [Run(**d) for d in await self.app.store.list("run", limit=10_000) if d["status"] in (RunStatus.QUEUED, RunStatus.RUNNING)]
        stale = [run for run in stale if not _alive(run.pid)]  # another live process may be in the middle of one
        for run in stale:
            self._close_out(run)
        return len(stale)

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    # ------------------------------------------------------------------ lifecycle

    async def _model_for(self, spec: RunSpec) -> LLMGateway | None:
        """The model gateway this run uses, or None for a model-free run.

        `auto` uses Claude when credentials are present and no model otherwise; a provider
        named explicitly must be usable, or the run is blocked with the reason."""
        if spec.brain == "heuristic":
            return None
        gateway = self.app.gateway("ollama" if spec.brain == "ollama" else "claude", spec.model)
        reason = await gateway.provider.unavailable()
        if reason and spec.brain != "auto":
            raise RunBlocked(reason)
        return None if reason else gateway

    def _save(self, run: Run) -> None:
        self.app.store.put("run", run.id, run, parent=run.project_id, created=run.created_at)

    async def _execute(self, run: Run, project: Project) -> None:
        spec, s = run.spec, self.app.settings
        bus = self.app.bus
        bus.publish(run.id, "run.queued", {"run": run.model_dump(mode="json")})
        try:
            async with self._admission:
                env = project.environment(spec.environment)
                gateway = await self._model_for(spec)
                saved = await self.app.store.get("pmap", f"{project.id}:{env.name}")
                origin = origin_of(env.base_url)
                pmap = ProductMap.from_json(saved, s.max_states_per_template) if saved and saved.get("origin") == origin else ProductMap(origin, s.max_states_per_template)
                budget = Budget(max_steps=spec.max_steps, max_seconds=spec.max_minutes * 60, max_usd=spec.max_usd)
                self._budgets[run.id] = budget
                ctx = RunContext(run=run, project=project, env=env, pmap=pmap, budget=budget, gateway=gateway, bus=bus)
                run.status, run.started_at = RunStatus.RUNNING, now()
                self._phase(ctx, "preparing")
                await self._preflight(ctx)
                pipeline = {RunKind.DISCOVERY: self._discovery, RunKind.DEEP_SCAN: self._deep_scan, RunKind.SCENARIOS: self._scenarios}[spec.kind]
                await pipeline(ctx)
                self.app.store.put("pmap", f"{project.id}:{env.name}", pmap.to_json(), parent=project.id)
        except asyncio.CancelledError:
            run.status, run.error = RunStatus.CANCELED, "Canceled."
        except RunBlocked as exc:
            run.status, run.error = RunStatus.BLOCKED, str(exc)
        except Exception as exc:
            while isinstance(exc, BaseExceptionGroup) and exc.exceptions:  # unwrap TaskGroup failures to the real cause
                exc = exc.exceptions[0]  # type: ignore[assignment]
            run.status, run.error = RunStatus.ERROR, f"{type(exc).__name__}: {exc}"[:500]
        finally:
            self._budgets.pop(run.id, None)
            run.finished_at, run.phase = now(), "finished"
            self._save(run)
            bus.publish(run.id, "run.finished", {"run": run.model_dump(mode="json")})
            with suppress(Exception):
                await asyncio.shield(self.app.store.flush())
            bus.end(run.id)

    async def _preflight(self, ctx: RunContext) -> None:
        """Fail fast, and say so plainly, when there is no app to test."""
        if reason := await netguard.refusal(ctx.env.base_url):
            raise RunBlocked(f"Catchbunny does not open {ctx.env.base_url}: {reason}. No app under test lives there.")
        try:
            async with self.app.pool.context(browser=ctx.run.spec.browser) as bctx:
                session = await Session.open(bctx, base_url=ctx.env.base_url, device="desktop", settings=self.app.settings)
                opened = await session.act(Action(kind="navigate", value=(ctx.run.spec.areas or ["/"])[0]), live=False)
        except EngineUnavailable as exc:
            raise RunBlocked(str(exc)) from exc
        if not opened.ok:
            raise RunBlocked(f"The app at {ctx.env.base_url} could not be opened ({opened.error}). Start it, or check the URL.")

    def _phase(self, ctx: RunContext, phase: str) -> None:
        ctx.run.phase = phase
        self._save(ctx.run)
        ctx.emit("run.phase", {"phase": phase, "budget": ctx.budget.as_dict()})

    # ------------------------------------------------------------------ sessions

    @asynccontextmanager
    async def session(self, ctx: RunContext, mission: Mission, record_dir: str | None = None, exclusive: bool = False) -> AsyncIterator[Session]:
        """A browser session for `mission`.

        Exploration (`exclusive=False`) starts from a copy of the persona's one
        shared sign-in: cheap, and good enough for finding candidates.

        Work whose verdict depends on state (`exclusive=True`: verification,
        minimisation, scenarios) must start from the baseline a later run will
        also have. For a signed-in persona that means its own fresh sign-in -
        a new server session with nothing left over from the hunt, so a
        reproduction can never be "minimised" into relying on a cart someone
        else filled - and the persona's account lock, so two replays never
        rearrange the same account under each other.
        """
        persona = ctx.project.persona(mission.persona)
        fresh = exclusive and persona.credential is not None
        resets = exclusive and bool(ctx.env.reset_path)
        state = None if fresh else await self._signed_in_state(ctx, persona, mission.device)
        # A reset puts the whole app back, so replays that reset take turns; otherwise only one account's do.
        lock = ctx.account_locks.setdefault("*" if resets else persona.name, asyncio.Lock()) if fresh or resets else None
        if lock is not None:
            await lock.acquire()
        started = time.monotonic()
        try:
            async with self.app.pool.context(mission.device, storage_state=state, record_dir=record_dir, browser=mission.browser) as bctx:
                session = await Session.open(
                    bctx,
                    base_url=ctx.env.base_url,
                    device=mission.device,
                    settings=self.app.settings,
                    artifacts=self.app.artifacts,
                    credential=persona.credential,
                )
                if fresh:
                    assert persona.credential is not None
                    if not await session.login(ctx.env.login, persona.credential):
                        raise RunBlocked(f"Sign-in as '{persona.name}' failed at {ctx.env.login.path}.")
                if resets and (problem := await session.reset(ctx.env.reset_path)):
                    ctx.warn(f"The reset at {ctx.env.reset_path} did not work ({problem}); replays ran on whatever data was there.")
                if fresh or resets:
                    session.forget_signals()
                yield session
        finally:
            if lock is not None:
                lock.release()
            ctx.budget.browser_seconds += time.monotonic() - started

    async def _signed_in_state(self, ctx: RunContext, persona: Persona, device: str) -> dict[str, Any] | None:
        if persona.credential is None:
            return None
        lock = ctx.login_locks.setdefault(persona.name, asyncio.Lock())
        async with lock:  # single-flight: concurrent testers of one persona trigger one sign-in
            if persona.name not in ctx.storage:
                state: dict[str, Any] | None = None
                async with self.app.pool.context(device, browser=ctx.run.spec.browser) as bctx:
                    session = await Session.open(bctx, base_url=ctx.env.base_url, device=device, settings=self.app.settings, credential=persona.credential)
                    if await session.login(ctx.env.login, persona.credential):
                        state = await bctx.storage_state()  # type: ignore[assignment]
                ctx.storage[persona.name] = state
                if state is None:
                    ctx.blocked[persona.name] = f"Sign-in as '{persona.name}' failed at {ctx.env.login.path}."
                ctx.emit("login", {"persona": persona.name, "ok": state is not None})
            return ctx.storage[persona.name]

    def _brain(self, ctx: RunContext, mission: Mission, scenario: Scenario | None = None, first_step: int = 0) -> Brain:
        brief = Brief(
            mission=mission,
            base_url=ctx.env.base_url,
            policy=(scenario.policy if scenario and scenario.policy else ctx.project.policy),
            persona=ctx.project.persona(mission.persona).public(),
            knowledge=ctx.project.knowledge,
            goal=ctx.run.spec.goal,
            scenario=scenario,
            first_step=first_step,
            areas=ctx.run.spec.areas,
        )
        if mission.kind in ("explore", "fuzz") or ctx.gateway is None:
            return HeuristicBrain(brief, ctx.pmap)
        return ModelBrain(brief, ctx.gateway, ctx.budget)

    # ------------------------------------------------------------------ swarm

    async def _mission(self, ctx: RunContext, mission: Mission) -> MissionResult:
        mission.run_id = ctx.run.id
        result = MissionResult(mission=mission)
        try:
            async with self.session(ctx, mission) as session:
                if mission.persona in ctx.blocked:
                    mission.status, mission.verdict, mission.summary = "done", Verdict.BLOCKED, ctx.blocked[mission.persona]
                    ctx.emit("mission.finished", {"mission": mission.model_dump(mode="json")})
                else:
                    tester = Tester(
                        mission=mission,
                        session=session,
                        brain=self._brain(ctx, mission),
                        pmap=ctx.pmap,
                        budget=ctx.budget,
                        policy=ctx.project.policy,
                        emit=ctx.emit,
                        screenshots=ctx.run.spec.screenshots,
                        run_id=ctx.run.id,
                    )
                    result = await tester.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # browser could not open, context crashed, ...
            mission.status, mission.verdict, mission.summary = "done", Verdict.BLOCKED, f"{type(exc).__name__}: {exc}"[:300]
            ctx.emit("mission.finished", {"mission": mission.model_dump(mode="json")})
        self.app.store.put("mission", mission.id, mission, parent=ctx.run.id, created=mission.started_at or now())
        self.app.store.put("steps", mission.id, {"steps": [st.model_dump(mode="json") for st in result.trace]}, parent=ctx.run.id)
        return result

    async def _swarm(self, ctx: RunContext, missions: list[Mission]) -> list[MissionResult]:
        queue = deque(missions)
        results: list[MissionResult] = []

        async def worker() -> None:
            while queue and not ctx.budget.exhausted:
                results.append(await self._mission(ctx, queue.popleft()))

        async with asyncio.TaskGroup() as group:
            for _ in range(min(ctx.run.spec.testers, len(missions))):
                group.create_task(worker())
        return results

    def _explorers(self, ctx: RunContext, kind: str, count: int, steps_each: int) -> list[Mission]:
        """At least `count` missions of `kind`, covering every scope.

        Explorers cover persona x device (each combination is a different app to
        look at). Fuzzers cover personas on the first device only: what a server
        does with hostile input does not depend on the viewport.
        """
        personas, devices = ctx.personas(), ctx.run.spec.devices or ["desktop"]
        scopes = [(p, d) for d in (devices if kind == "explore" else devices[:1]) for p in personas]
        areas = ctx.run.spec.areas
        known = sorted({ctx.pmap.relative(n) for n in ctx.pmap.states.values() if n.explorable and (not areas or any(n.path.startswith(a) for a in areas))}, key=len)[: max(count, 1)]
        missions = []
        for i in range(max(count, len(scopes))):
            persona, device = scopes[i % len(scopes)]
            home = ctx.run.spec.areas[0] if ctx.run.spec.areas else "/"  # a focused run starts inside its area
            start = known[i % len(known)] if known and i else home  # spread over screens earlier runs already found
            title = f"{'Explore' if kind == 'explore' else 'Fuzz forms'} as {persona.name} on {device}"
            missions.append(Mission(kind=kind, title=title, persona=persona.name, device=device, browser=ctx.run.spec.browser, start_path=start, max_steps=steps_each))  # type: ignore[arg-type]
        return missions

    async def _triage(self, ctx: RunContext, results: list[MissionResult]) -> list[Finding]:
        spec = ctx.run.spec
        ctx.hunt_seconds = ctx.hunt_seconds or ctx.budget.elapsed
        triage = Triage(
            run_id=ctx.run.id,
            project_id=ctx.project.id,
            open_session=lambda mission, record_dir=None: self.session(ctx, mission, record_dir, exclusive=True),
            record=spec.record,
            verify_runs=spec.verify_runs,
            minimize=spec.minimize,
            max_replays=self.app.settings.minimize_max_replays,
            concurrency=spec.testers,
            anonymous={p.name for p in ctx.project.personas if p.credential is None},
        )
        findings: list[Finding] = []
        try:
            async with asyncio.timeout(max(90.0, spec.max_minutes * 60)):
                async for f in triage.run(results):
                    findings.append(f)
                    self.app.store.put("finding", f.id, f, parent=ctx.run.id, created=f.created_at)
                    ctx.emit("finding", {"finding": f.model_dump(mode="json")})
        except TimeoutError:
            ctx.emit("run.warning", {"message": "Triage time box reached; some candidates were not verified."})
        ctx.replays = triage.replays
        return rank(findings)

    def _finish(self, ctx: RunContext, results: list[MissionResult], findings: list[Finding], extra: dict[str, Any] | None = None) -> None:
        run, spec, b = ctx.run, ctx.run.spec, ctx.budget
        gate = _SEVERITY_RANK[spec.gate]
        blocking = [f for f in findings if f.status is FindingStatus.CONFIRMED and _SEVERITY_RANK[f.severity] <= gate]
        blocked = [{"mission": r.mission.title, "reason": r.mission.summary} for r in results if r.mission.verdict is Verdict.BLOCKED]
        # Findings fail the run. Otherwise a run that could not test part of what it was
        # asked to (a persona that cannot sign in, say) is blocked - never silently "passed".
        run.status = RunStatus.FAILED if blocking else RunStatus.BLOCKED if blocked else RunStatus.PASSED
        if blocked and not blocking:
            run.error = f"{len(blocked)} of {len(results)} missions could not run: {blocked[0]['reason']}"
        hunt_seconds = max(ctx.hunt_seconds or b.elapsed, 1e-6)
        run.summary = {
            "verdict": "blocked by findings" if blocking else "partly untested" if blocked else "no blocking findings",
            "blocked_missions": blocked,
            "gate": spec.gate.value,
            "blocking_findings": [f.id for f in blocking],
            "missions": len(results),
            "brains": sorted({r.mission.brain for r in results if r.mission.brain}),
            "browser": spec.browser,
            "model": ctx.gateway.provider.model if ctx.gateway else "",
            "findings": summarize(findings),
            "coverage": ctx.pmap.coverage(),
            "budget": b.as_dict(),
            "stop_reason": b.stop_reason or "frontier exhausted",
            "hunt_seconds": round(hunt_seconds, 2),
            "triage_seconds": round(max(0.0, b.elapsed - hunt_seconds), 2),
            "steps_per_second": round(b.steps / hunt_seconds, 2),
            "verification_replays": ctx.replays,
            "pool": self.app.pool.stats(),
            "store": {"writes": self.app.store.writes, "batches": self.app.store.batches},
            **(extra or {}),
        }

    # ------------------------------------------------------------------ pipelines

    async def _discovery(self, ctx: RunContext) -> None:
        spec = ctx.run.spec
        self._phase(ctx, "exploring")
        missions = self._explorers(ctx, "explore", spec.testers, max(20, 2 * spec.max_steps // spec.testers))
        results = await self._swarm(ctx, missions)
        self._phase(ctx, "triage")
        findings = await self._triage(ctx, results)
        self._phase(ctx, "drafting scenarios")
        drafts = await self._draft_scenarios(ctx, results)
        self._finish(ctx, results, findings, {"scenarios_drafted": [d.id for d in drafts]})

    async def _deep_scan(self, ctx: RunContext) -> None:
        spec = ctx.run.spec
        n = spec.testers
        results: list[MissionResult] = []
        if ctx.gateway is not None:
            # Phase 1 (cheap, no model): map the app so the planner has something to plan against.
            self._phase(ctx, "mapping")
            share = max(20, int(spec.max_steps * 0.3) // n)
            results += await self._swarm(ctx, self._explorers(ctx, "explore", n, share))
            # Phase 2: goal-directed model testers, plus fuzzers sweeping every form.
            self._phase(ctx, "planning")
            personas = [p.public() for p in ctx.personas()]
            charters = await plan_charters(
                ctx.gateway, goal=spec.goal, map_brief=ctx.pmap.brief(), personas=personas, knowledge=ctx.project.knowledge, count=2 * n, budget=ctx.budget
            )
            names = {p["name"] for p in personas}
            left = max(1, spec.max_steps - ctx.budget.steps)
            per = max(15, left // max(1, len(charters) + max(1, n // 2)))
            missions = [
                Mission(
                    kind="charter",
                    title=c["title"][:120],
                    charter=c["charter"],
                    persona=c["persona"] if c["persona"] in names else personas[0]["name"],
                    device=(spec.devices or ["desktop"])[i % len(spec.devices or ["desktop"])],
                    browser=spec.browser,
                    start_path=c.get("start_path") or "/",
                    max_steps=per,
                )
                for i, c in enumerate(charters)
            ]
            missions += self._explorers(ctx, "fuzz", max(1, n // 2), per)
            if len(missions) > 1:
                await ctx.gateway.prewarm(ctx.budget)  # the shared prefix is paid for once, then every tester reuses it
            self._phase(ctx, "hunting")
            results += await self._swarm(ctx, missions)
        else:
            self._phase(ctx, "hunting")
            half = max(1, n // 2)
            each = max(20, 2 * spec.max_steps // max(1, n))
            missions = self._explorers(ctx, "explore", max(1, n - half), each) + self._explorers(ctx, "fuzz", half, each)
            results += await self._swarm(ctx, missions)
        self._phase(ctx, "triage")
        findings = await self._triage(ctx, results)
        self._finish(ctx, results, findings)

    async def _scenarios(self, ctx: RunContext) -> None:
        spec, store = ctx.run.spec, self.app.store
        docs = await store.list("scenario", parent=ctx.project.id)
        scenarios = [Scenario(**d) for d in docs if not spec.scenario_ids or d["id"] in spec.scenario_ids]
        if not scenarios:
            raise RuntimeError("no scenarios to run")
        self._phase(ctx, "running scenarios")
        queue = deque(scenarios)
        reports: list[ScenarioReport] = []

        async def worker() -> None:
            while queue and not ctx.budget.exhausted:
                reports.append(await self._scenario(ctx, queue.popleft()))

        async with asyncio.TaskGroup() as group:
            for _ in range(min(spec.testers, len(scenarios))):
                group.create_task(worker())
        verdicts = [r.verdict for r in reports]
        if any(v is Verdict.FAILED for v in verdicts):
            ctx.run.status = RunStatus.FAILED
        elif all(v is Verdict.PASSED for v in verdicts) and len(reports) == len(scenarios):
            ctx.run.status = RunStatus.PASSED
        else:
            ctx.run.status = RunStatus.BLOCKED
        learned = sum(sum(s.mode in ("learned", "healed") for s in r.steps) for r in reports)
        ctx.run.summary = {
            "scenarios": [r.as_dict() for r in reports],
            "passed": sum(v is Verdict.PASSED for v in verdicts),
            "failed": sum(v is Verdict.FAILED for v in verdicts),
            "blocked": sum(v is Verdict.BLOCKED for v in verdicts),
            "inconclusive": sum(v is Verdict.INCONCLUSIVE for v in verdicts),
            "not_run": len(scenarios) - len(reports),
            "steps_replayed_without_model": learned,
            "steps_healed": sum(sum(s.mode == "healed" for s in r.steps) for r in reports),
            "budget": ctx.budget.as_dict(),
            "pool": self.app.pool.stats(),
        }

    async def _scenario(self, ctx: RunContext, scenario: Scenario) -> ScenarioReport:
        store, ttl = self.app.store, self.app.settings.learned_ttl_days * 86400
        # A scenario runs in the engine its file names, unless this run asks for another one.
        engine = ctx.run.spec.browser if ctx.run.spec.browser != "chromium" else scenario.browser
        mission = Mission(run_id=ctx.run.id, kind="scenario", title=scenario.name, persona=scenario.persona, device=scenario.device, browser=engine, start_path=scenario.start, scenario_id=scenario.id, max_steps=80)
        report = ScenarioReport(scenario=scenario, mission=mission)
        # Learning shipped in the file is overridden by fresher learning from this project's own passing runs.
        learned = {ls.step_hash: ls for ls in scenario.learned}
        cache_id = f"{ctx.project.id}:{scenario.name}:{scenario.persona}"
        cached = await store.get("learned", cache_id)
        for raw in (cached or {}).get("steps", []):
            entry = LearnedStep(**raw)
            if now() - entry.learned_at < ttl:
                learned[entry.step_hash] = entry
        ctx.emit("scenario.started", {"scenario_id": scenario.id, "name": scenario.name})
        try:
            async with self.session(ctx, mission, exclusive=True) as session:
                if scenario.persona in ctx.blocked:
                    report.verdict, report.reason = Verdict.BLOCKED, ctx.blocked[scenario.persona]
                else:
                    factory = (lambda m, sc, first: self._brain(ctx, m, sc, first)) if ctx.gateway else None
                    report = await ScenarioRunner(
                        session=session,
                        scenario=scenario,
                        mission=mission,
                        learned=learned,
                        pmap=ctx.pmap,
                        budget=ctx.budget,
                        policy=scenario.policy or ctx.project.policy,
                        emit=ctx.emit,
                        brain_factory=factory,
                        gateway=ctx.gateway,
                        run_id=ctx.run.id,
                    ).run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            report.verdict, report.reason = Verdict.BLOCKED, f"{type(exc).__name__}: {exc}"[:300]
        if report.verdict is Verdict.PASSED and report.learned:  # only a passing run teaches; reuse refreshes the TTL
            fresh = [ls.model_copy(update={"learned_at": now()}) for ls in report.learned]
            store.put("learned", cache_id, {"steps": [ls.model_dump(mode="json") for ls in fresh]}, parent=ctx.project.id)
        mission.status, mission.verdict, mission.summary = "done", report.verdict, report.reason
        store.put("mission", mission.id, mission, parent=ctx.run.id, created=now())
        ctx.emit("scenario.finished", report.as_dict())
        return report

    # ------------------------------------------------------------------ discovery -> scenario drafts

    async def _draft_scenarios(self, ctx: RunContext, results: list[MissionResult], limit: int = 8) -> list[Scenario]:
        """Turn observed journeys into saved scenarios, without a model.

        A journey is a form submission that changed the screen and raised no
        defect. Its local action slice is replayed in a fresh browser; only a
        slice that reaches the same screen again becomes a scenario, with typed
        checks on where it ended up.
        """
        candidates: dict[str, tuple[Mission, list[Any], str]] = {}
        for r in results:
            for i, step in enumerate(r.trace):
                a = step.action
                if a.kind != "click" or not a.target or a.target.role != "button" or not step.ok or step.signals:
                    continue
                if not step.state_after or step.url_after == step.url_before and not a.target.attrs.get("form"):
                    continue
                start = max((j for j in range(i + 1) if r.trace[j].action.kind == "navigate"), default=0)
                slice_ = [st.action for st in r.trace[start : i + 1] if st.ok]
                key = f"{path_pattern(step.url_before)}|{a.target.key()}"
                if slice_ and slice_[0].kind == "navigate" and key not in candidates:
                    candidates[key] = (r.mission, slice_, step.url_after)

        async def verify(mission: Mission, actions: list[Any], url_after: str) -> Scenario | None:
            async with self.session(ctx, mission, exclusive=True) as session:
                out = await replay(session, actions)
                if out.failed_at >= 0 or out.signals or out.final is None or path_pattern(out.final.url) != path_pattern(url_after):
                    return None
                final = out.final
            body = actions[1:]
            button = body[-1].target
            checks = [Check(kind="url_contains", value=url_path(final.url))]
            if final.headings:
                checks.append(Check(kind="text_visible", value=final.headings[0].split(": ", 1)[-1]))
            steps = [a.describe() for a in body]
            return Scenario(
                project_id=ctx.project.id,
                name=f'{mission.persona} can use "{button.name}" on {path_pattern(actions[0].value)}'[:120],
                persona=mission.persona,
                device=mission.device,
                browser=mission.browser,
                start=actions[0].value,
                steps=steps,
                checks=checks,
                learned=[LearnedStep(step_hash=step_hash(t), actions=[a], source_run=ctx.run.id) for t, a in zip(steps, body, strict=True)],
                tags=["draft", "discovered"],
            )

        picked = list(candidates.values())[:limit]
        drafts = [d for d in await asyncio.gather(*(verify(*c) for c in picked), return_exceptions=True) if isinstance(d, Scenario)]
        for d in drafts:
            self.app.store.put("scenario", d.id, d, parent=ctx.project.id, created=d.created_at)
            ctx.emit("scenario.drafted", {"scenario_id": d.id, "name": d.name})
        return drafts

    # ------------------------------------------------------------------ on-demand re-verification

    async def rerun_finding(self, project: Project, finding: Finding, runs: int = 1) -> dict[str, Any]:
        """"Rerun in a fresh browser": is this bug still there right now?"""
        run = Run(id=finding.run_id, project_id=project.id, spec=RunSpec())
        env = project.environment()
        ctx = RunContext(
            run=run, project=project, env=env, pmap=ProductMap(origin_of(env.base_url)), budget=Budget(max_steps=10**6, max_seconds=300), gateway=None, bus=self.app.bus
        )
        mission = Mission(run_id=run.id, persona=finding.persona, device=finding.device, browser=finding.browser)

        async def once() -> bool:
            async with self.session(ctx, mission, exclusive=True) as session:
                if finding.check is not None and finding.check.kind != "no_signal":
                    out = await replay(session, finding.repro)
                    if out.final is None or out.failed_at >= 0:
                        return False
                    result = await session.run_check(finding.check, out.final, out.signals)
                    return result.evaluable and not result.passed
                out = await replay(session, finding.repro, until=lambda signals: any(s.signature == finding.signature for s in signals))
                return out.hit

        hits = sum(await asyncio.gather(*(once() for _ in range(runs))))
        return {"finding_id": finding.id, "runs": runs, "reproduced": hits, "still_present": hits > 0}
