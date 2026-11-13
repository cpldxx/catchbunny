"""MCP server: Catchbunny as tools for a coding agent.

This closes the loop the product exists for. An agent that just wrote a change
can ask "did I break anything?", read a finding precise enough to act on, fix
it, and ask "is it gone?" - without a human relaying anything:

    scan            -> findings (verified, minimised), or `running` and a run id
    run_status      -> the rest of a run that outlived one tool call
    get_finding     -> expected / actual / steps / evidence, phrased as a task
    verify_fix      -> replay the reproduction against the app as it is now
    keep_as_scenario-> the regression test, as a file the agent can commit
    run_scenarios   -> run committed scenario files (no model calls)

Results are deliberately small: an agent's context is the scarce resource, so
lists carry ids and one-line summaries, and detail is fetched per finding.

No tool holds a call open for the length of a run. Clients cut tool calls off
(Codex after 60 seconds by default, proxies in front of a remote server after
about 100), and a scan takes minutes: `scan` waits a bounded time and hands
back a run id, and `run_status` waits for the rest.

Two transports, one server:

    catchbunny mcp                       # stdio: the agent starts it (Claude Code, Codex, Claude Desktop)
    catchbunny mcp --http --token auto   # Streamable HTTP at /mcp: connectors that reach it over the network

`catchbunny connect` writes the stdio registration into each agent's own config.
"""

from __future__ import annotations

import asyncio
import hmac
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from .domain import BROWSERS, Finding, Policy, Project, Run, RunKind, RunSpec, RunStatus, Scenario
from .engine.orchestrator import App
from .engine.scenarios import finding_to_scenario, fix_prompt, scenario_from_yaml, scenario_to_yaml
from .engine.triage import rank
from .projects import ensure_project

# Agents read this when they connect; some keep only the start, so the first paragraph stands on its own.
INSTRUCTIONS = """Catchbunny tests a running web app with parallel browser testers and reports only bugs it has reproduced in a fresh browser. Use it after changing UI or API code, or when asked to check an app for bugs. Loop: scan(url) -> while status is `running`, run_status(run_id) -> get_finding(id) for each confirmed finding -> fix the code -> verify_fix(id) -> keep_as_scenario(id) and commit the file. The app must already be running at the URL you pass: start the dev server first.

A run that says `blocked` tested nothing, or only part of what was asked: read `error`.
`areas` keeps a scan inside the paths you changed. Credentials are for a TEST account only.
Catchbunny opens URLs from the machine it runs on. When it is a remote connector, `localhost` is that machine, not the user's."""

LOOKS = ToolAnnotations(read_only_hint=True, open_world_hint=False)  # reads Catchbunny's own records
ACTS = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True)  # drives browsers against the app, within its policy
UNFINISHED = (RunStatus.QUEUED, RunStatus.RUNNING)


def _brief(f: Finding) -> dict[str, Any]:
    return {"id": f.id, "severity": f.severity.value, "status": f.status.value, "title": f.title, "url": f.url, "steps": len(f.repro)}


def create_mcp(core: App | None = None, *, manage: bool = False, max_wait: float = 3600.0, **auth: Any) -> MCPServer[Any]:
    """`max_wait` caps how long one tool call may wait for a run: keep it under whatever cuts requests off in front of the server.

    `auth` is passed to the SDK's server as it is (`auth`, `auth_server_provider`, `token_verifier`): a team
    server uses it to require a key or a signed-in connector."""
    owned = core is None or manage
    app = core or App()
    tidying: set[asyncio.Task[None]] = set()

    @asynccontextmanager
    async def lifespan(_: MCPServer[Any]) -> AsyncIterator[App]:
        if owned:
            await app.start()
        try:
            yield app
        finally:
            if owned:
                await app.stop()

    server: MCPServer[Any] = MCPServer(name="catchbunny", instructions=INSTRUCTIONS, lifespan=lifespan, **auth)

    async def finding(finding_id: str) -> tuple[Finding, Project]:
        doc = await app.store.get("finding", finding_id)
        if doc is None:
            raise ToolError(f"finding {finding_id} not found; call scan first, or check the id")  # anticipated: the text reaches the agent
        f = Finding(**doc)
        return f, app.load_project(await app.store.get("project", f.project_id) or {})

    async def report(run: Run) -> dict[str, Any]:
        """A run as an agent should see it: finished with its results, or still going with what there is so far."""
        findings = rank([Finding(**d) for d in await app.store.list("finding", parent=run.id)])
        shown = [_brief(f) for f in findings if f.status.value != "not_reproduced"]
        if run.status in UNFINISHED:
            return {
                "status": "running",
                "run_id": run.id,
                "phase": run.phase,
                **app.runs.progress(run.id),
                "findings": shown,
                "next": f"Still running. Call run_status(run_id='{run.id}'): it waits for the run, and returns the findings when it is done.",
            }
        summary = run.summary
        if run.spec.kind is RunKind.SCENARIOS:
            results = [{k: s[k] for k in ("name", "verdict", "reason")} for s in summary.get("scenarios", [])]
            return {"status": run.status.value, "error": run.error, "run_id": run.id, "scenarios": results}
        return {
            "status": run.status.value,
            "error": run.error,
            "run_id": run.id,
            "coverage": summary.get("coverage", {}),
            "steps": summary.get("budget", {}).get("steps", 0),
            "stop_reason": summary.get("stop_reason", ""),
            "findings": shown,
            "next": "Call get_finding(id) for each confirmed finding." if findings else "",
        }

    def patience(wait_seconds: float) -> float:
        return max(0.0, min(float(wait_seconds), max_wait))

    @server.tool(title="Scan a web app for bugs", annotations=ACTS)
    async def scan(
        url: str,
        goal: str = "",
        areas: list[str] | None = None,
        testers: int = 4,
        max_steps: int = 300,
        max_minutes: float = 4.0,
        devices: list[str] | None = None,
        username: str = "",
        password: str = "",
        login_path: str = "/login",
        policy: str = "safe_writes",
        brain: str = "auto",
        model: str = "",
        browser: str = "chromium",
        reset_path: str = "",
        wait_seconds: float = 45.0,
    ) -> dict[str, Any]:
        """Hunt for bugs in the web app running at `url` and return verified findings.

        Returns within about `wait_seconds`. A scan usually takes longer than one tool call may:
        if status is `running`, call run_status(run_id) until it is not. The run goes on meanwhile.
        `areas` are path prefixes to stay within, e.g. ["/checkout"] - use them to test what you just changed.
        `username`/`password` are a TEST account; with them a signed-in persona is tested too.
        `policy`: read_only | safe_writes | full - what the testers may do to the app's data.
        `brain`: auto | heuristic | claude | ollama - who drives the goal-directed testers; `model` picks an Ollama tag.
        `browser`: chromium | firefox | webkit - the engine to test in. webkit is the engine Safari is built on.
        `reset_path`: a path on the app that puts its test data back, e.g. "/api/test/reset"; called before every replay.
        Returns status (running | passed | failed | blocked | error), a coverage summary and one line per finding.
        """
        if browser not in BROWSERS:
            raise ToolError(f"browser must be one of {', '.join(BROWSERS)}")
        project = await ensure_project(app, url, user=username, password=password, login_path=login_path, policy=Policy(policy), reset_path=reset_path or None)
        spec = RunSpec(kind=RunKind.DEEP_SCAN, goal=goal, areas=areas or [], testers=testers, max_steps=max_steps, max_minutes=max_minutes, devices=devices or ["desktop"], brain=brain, model=model, browser=browser)  # type: ignore[arg-type]
        run = await app.runs.start(project, spec)
        return await report(await app.runs.wait(run.id, patience(wait_seconds)))

    @server.tool(title="Wait for a run and get its result", annotations=LOOKS)
    async def run_status(run_id: str, wait_seconds: float = 45.0) -> dict[str, Any]:
        """The result of a run started by scan or run_scenarios. Waits up to `wait_seconds` for it to finish.

        While status is `running`, call this again. When it is anything else, the result is the same as the
        tool that started the run would have returned.
        """
        try:
            return await report(await app.runs.wait(run_id, patience(wait_seconds)))
        except KeyError:
            raise ToolError(f"run {run_id} not found; use the run_id that scan or run_scenarios returned") from None

    @server.tool(title="List a run's findings", annotations=LOOKS)
    async def list_findings(run_id: str) -> list[dict[str, Any]]:
        """One line per finding of a run, most important first."""
        return [_brief(f) for f in rank([Finding(**d) for d in await app.store.list("finding", parent=run_id)])]

    @server.tool(title="Read one finding", annotations=LOOKS)
    async def get_finding(finding_id: str) -> dict[str, Any]:
        """Everything needed to fix one finding: expected vs actual, minimal steps, the evidence, and a ready-made task description."""
        f, project = await finding(finding_id)
        ev = f.evidence
        signal = ev.get("signal", {})
        return {
            **_brief(f),
            "expected": f.expected,
            "actual": f.actual,
            "persona": f.persona,
            "device": f.device,
            "browser": f.browser,
            "reproduced": f"{f.verification.reproduced}/{f.verification.runs} fresh-browser replays",
            "steps_to_reproduce": f.repro_text(),
            "evidence": {
                "message": signal.get("message", ""),
                "stack": signal.get("detail", {}).get("stack", ""),
                "failed_requests": [n for n in ev.get("network", []) if n["status"] == "failed" or (isinstance(n["status"], int) and n["status"] >= 400)][:10],
                "console": [c for c in ev.get("console", []) if c["level"] in ("error", "exception", "warning")][:10],
                "artifacts": {k: ev[k] for k in ("video", "trace", "screenshot") if ev.get(k)},
            },
            "task": fix_prompt(f, project.environments[0].base_url),
        }

    @server.tool(title="Check whether a bug is fixed", annotations=ACTS)
    async def verify_fix(finding_id: str, runs: int = 2) -> dict[str, Any]:
        """Replay a finding's reproduction against the app as it is now. Use after changing the code.

        `still_present: false` means the reproduction no longer triggers the defect in any of `runs` fresh browsers.
        """
        f, project = await finding(finding_id)
        result = await app.runs.rerun_finding(project, f, max(1, min(runs, 5)))
        result["verdict"] = "still broken" if result["still_present"] else "fixed: the reproduction no longer triggers the defect"
        return result

    @server.tool(title="Turn a finding into a regression test", annotations=LOOKS)
    async def keep_as_scenario(finding_id: str) -> dict[str, Any]:
        """Turn a finding into a regression scenario. Returns YAML to save in the repository (e.g. qa/<name>.yaml) and run with run_scenarios."""
        f, _ = await finding(finding_id)
        scenario = finding_to_scenario(f)
        name = "".join(c if c.isalnum() else "-" for c in f.title.lower()).strip("-")[:60]
        return {"suggested_path": f"qa/{name or f.id}.yaml", "yaml": scenario_to_yaml(scenario)}

    @server.tool(title="Run regression scenarios", annotations=ACTS)
    async def run_scenarios(url: str, scenarios_yaml: list[str], username: str = "", password: str = "", login_path: str = "/login", wait_seconds: float = 45.0) -> dict[str, Any]:
        """Run scenario files (their YAML contents) against the app at `url`. Learned steps replay without any model call.

        Returns status and, per scenario, verdict (passed | failed | blocked | inconclusive) with the reason.
        If status is `running` after `wait_seconds`, call run_status(run_id) for the verdicts.
        """
        project = await ensure_project(app, url, user=username, password=password, login_path=login_path)
        scenarios: list[Scenario] = []
        for i, text in enumerate(scenarios_yaml):
            try:
                scenario = scenario_from_yaml(text, project.id)
            except Exception as exc:
                raise ToolError(f"scenarios_yaml[{i}] is not a valid scenario: {exc}") from exc
            await app.store.put_now("scenario", scenario.id, scenario, parent=project.id, created=scenario.created_at)
            scenarios.append(scenario)
        started = await app.runs.start(project, RunSpec(kind=RunKind.SCENARIOS, scenario_ids=[s.id for s in scenarios]))

        async def tidy() -> None:  # the files are the source of truth; keep no copies, however long the run takes
            with suppress(Exception):
                await app.runs.wait(started.id)
                for s in scenarios:
                    await app.store.delete("scenario", s.id)

        task = asyncio.create_task(tidy())
        tidying.add(task)
        task.add_done_callback(tidying.discard)
        run = await app.runs.wait(started.id, patience(wait_seconds))
        if run.status not in UNFINISHED:
            await task
        return await report(run)

    return server


# ---------------------------------------------------------------- over HTTP

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
Asgi = Callable[[Scope, Receive, Send], Awaitable[None]]

HTTP_MAX_WAIT = 50.0  # proxies and tunnels give up on a silent request after about 100 seconds


def access_token(data_dir: Path) -> str:
    """The token for this data directory, made on first use and kept, so a connector set up once keeps working."""
    path = data_dir / "mcp_token"
    if path.exists() and (token := path.read_text().strip()):
        return token
    data_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n")
    path.chmod(0o600)
    return token


class Gate:
    """Lets a request through only with the access token.

    The token is a bearer header where the client can send one (Claude Code, Codex, Claude's
    "request headers"), or the first segment of the path where it can only be given a URL:
    https://host/<token>/mcp. Whoever holds it can make this machine open any address it can
    reach, so there is no mode that serves the network without one.
    """

    def __init__(self, app: Asgi, token: str) -> None:
        self.app, self.token = app, token.encode()

    def _is(self, candidate: str) -> bool:
        return hmac.compare_digest(candidate.encode(), self.token)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        if path == "/":
            await _plain(send, 200, "Catchbunny MCP server. The endpoint is /mcp" + (" and needs the access token.\n" if self.token else ".\n"))
            return
        if not self.token:
            await self.app(scope, receive, send)
            return
        first, _, rest = path.lstrip("/").partition("/")
        if rest and self._is(first):
            await self.app({**scope, "path": "/" + rest, "raw_path": ("/" + rest).encode()}, receive, send)
            return
        header = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"authorization"), "")
        scheme, _, value = header.partition(" ")
        if scheme.lower() == "bearer" and self._is(value.strip()):
            await self.app(scope, receive, send)
            return
        await _plain(send, 401, "Missing or wrong access token.\n", [(b"www-authenticate", b"Bearer")])


async def _plain(send: Send, status: int, text: str, headers: list[tuple[bytes, bytes]] | None = None) -> None:
    body = text.encode()
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode()), *(headers or [])]})
    await send({"type": "http.response.body", "body": body})


def http_app(server: MCPServer[Any], *, token: str = "") -> Asgi:
    """The server as an ASGI app: Streamable HTTP at /mcp, one request at a time with no session to lose.

    Without a token only this machine may use it: requests must name localhost, which is what stops
    a web page from reaching it through the browser, and what a tunnel's requests never do. With a
    token the caller has proved itself, so any host name is accepted and a tunnel works.
    """
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False) if token else None
    return Gate(server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True, transport_security=security, host="127.0.0.1"), token)
