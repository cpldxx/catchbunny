"""HTTP API (FastAPI). Thin by design: every handler validates, delegates to
the engine, and serialises. Runs are asynchronous resources - POST returns at
once, progress arrives over Server-Sent Events with `Last-Event-ID` resume.

Errors use one machine-readable envelope so a coding agent (or a UI) can
branch on `code` and `retryable` instead of parsing prose.

Two ways to be served (`Serving`):

* **On your own machine** (the default). No accounts: the server listens on
  localhost and answers only requests addressed to localhost, which is what
  keeps a web page you happen to have open from driving it.
* **For a team** (`start --team`). Every `/v1` request needs a signed-in
  browser or a key; people join with the team's code; and the MCP endpoint at
  `/mcp` takes the same keys, or signs a connector in with OAuth.

Either way one process is the whole product: the site, the console, the API
and the MCP endpoint, on one port.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from http.cookies import SimpleCookie
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.datastructures import Headers

from .accounts import SESSION_DAYS, AccountError, Accounts, User
from .demo_shop import BUGS, PASSWORD, USER, Running
from .domain import BROWSERS, Environment, Finding, LoginRecipe, Persona, Policy, Project, Run, RunKind, RunSpec, RunStatus, Scenario
from .engine import relevance
from .engine.orchestrator import App
from .engine.scenarios import finding_to_scenario, fix_prompt, playwright_test, scenario_from_yaml, scenario_to_yaml
from .engine.triage import rank
from .projects import ensure_project

# The built site: in a checkout it is web/dist, rebuilt by `npm run build`; in an installed copy it was
# packed into the package as catchbunny/web (see pyproject.toml), so `catchbunny start` has its console.
_CHECKOUT_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
WEB_DIST = _CHECKOUT_DIST if (_CHECKOUT_DIST / "index.html").is_file() else Path(__file__).resolve().parent / "web"
TERMINAL = {RunStatus.PASSED, RunStatus.FAILED, RunStatus.BLOCKED, RunStatus.CANCELED, RunStatus.ERROR}
LOOPBACK = ("127.0.0.1", "localhost", "::1")
COOKIE = "catchbunny_session"
DEMO_NAME = "Mug & Co, the demo shop"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
# What anyone may ask a team server without signing in: whether it is up, what it is, and the way in.
OPEN_PATHS = ("/healthz", "/v1/meta", "/v1/docs", "/v1/openapi.json")
MCP_PATHS = ("/mcp", "/authorize", "/token", "/register", "/revoke")

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
Asgi = Callable[[Scope, Receive, Send], Awaitable[None]]


@dataclass(slots=True)
class Serving:
    """How this server is offered. The default is how tests and embedders get it: no accounts, no host check."""

    team: bool = False  # accounts on: every /v1 request needs a signed-in browser or a key
    public_url: str = ""  # where people and agents reach it, e.g. https://qa.example.com (a team server's own domain)
    local_only: bool = False  # answer only requests addressed to localhost (the default for `catchbunny start`)

    @property
    def secure(self) -> bool:
        return self.public_url.startswith("https://")

    @property
    def signs_connectors_in(self) -> bool:
        """OAuth needs HTTPS, except on the machine itself."""
        return self.team and (self.secure or (urlsplit(self.public_url).hostname or "") in LOOPBACK)


class ProjectIn(BaseModel):
    name: str
    base_url: str
    login: LoginRecipe = Field(default_factory=LoginRecipe)
    personas: list[Persona] = Field(default_factory=lambda: [Persona()])
    knowledge: str = ""
    policy: Policy = Policy.SAFE_WRITES
    reset_path: str = ""


class ProjectPatch(BaseModel):
    base_url: str | None = None
    reset_path: str | None = None
    login: LoginRecipe | None = None
    personas: list[Persona] | None = None
    knowledge: str | None = None
    policy: Policy | None = None


class ScenarioIn(BaseModel):
    yaml: str


class RelevanceIn(BaseModel):
    changed_files: list[str] = Field(default_factory=list)
    diff: str = ""


class OwnerIn(BaseModel):
    code: str
    name: str
    email: str
    password: str


class SignInIn(BaseModel):
    email: str
    password: str


class TeamPatch(BaseModel):
    name: str


class TokenIn(BaseModel):
    label: str = ""


class AnswerIn(BaseModel):
    approve: bool


def _refuse(scope: Scope, receive: Receive, send: Send, status: int, code: str, message: str) -> Awaitable[None]:
    return JSONResponse({"status": "error", "code": code, "message": message, "retryable": False}, status_code=status)(scope, receive, send)


def _hostname(host: str) -> str:
    """The name in a Host header, without the port: `localhost:8700`, `[::1]:8700`."""
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if ":" in host else host


class Guard:
    """Who is asking, checked before a request reaches a handler.

    It is plain ASGI and does its work before the app is called, so it never sits in the path of a
    response: a run's event stream passes through untouched.
    """

    def __init__(self, app: Asgi, serving: Serving, accounts: Accounts, bearer: Callable[[str], Awaitable[User | None]]) -> None:
        self.app, self.serving, self.accounts, self.bearer = app, serving, accounts, bearer
        self.origin = "{0.scheme}://{0.netloc}".format(urlsplit(serving.public_url)) if serving.public_url else ""

    async def identify(self, headers: Headers) -> tuple[User | None, str]:
        """(who, how): a key in the Authorization header, or the browser's session cookie."""
        scheme, _, value = headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return await self.bearer(value.strip()), "key"
        cookie = SimpleCookie(headers.get("cookie", "")).get(COOKIE)
        return (await self.accounts.session_user(cookie.value) if cookie else None), "cookie"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers, path = Headers(scope=scope), scope["path"]
        host = headers.get("host", "")
        if self.serving.local_only and _hostname(host) not in LOOPBACK:
            # A page on another site can point a name of its own at 127.0.0.1 and talk to a local server
            # through the browser. Such a request names that site, never localhost.
            await _refuse(scope, receive, send, 403, "FORBIDDEN", "This server answers only requests addressed to localhost. To use it from elsewhere, serve it for a team: catchbunny start --team")
            return
        state = scope.setdefault("state", {})
        state["user"] = None
        if self.serving.team:
            user, how = await self.identify(headers)
            state["user"] = user
            private = path.startswith("/v1/") and path not in OPEN_PATHS and not path.startswith("/v1/auth/")
            if private and user is None:
                await _refuse(scope, receive, send, 401, "UNAUTHENTICATED", "Sign in, or send a key in the Authorization header.")
                return
            origin = headers.get("origin", "")
            if how == "cookie" and scope["method"] not in SAFE_METHODS and origin and origin not in (self.origin, f"{scope.get('scheme', 'http')}://{host}"):
                await _refuse(scope, receive, send, 403, "FORBIDDEN", "This request came from another site.")  # a cookie is not consent to what another page asks for
                return
        await self.app(scope, receive, send)


def _client(request: Request) -> str:
    """The address a request came from. Behind a proxy that `--trust-proxy` names, uvicorn has already
    put the address the proxy saw here; from anyone else, a forwarded header is not believed."""
    return request.client.host if request.client else ""


def create_app(core: App | None = None, *, manage: bool = False, serving: Serving | None = None) -> FastAPI:
    owned = core is None or manage  # who starts and stops the engine
    app_core = core or App()
    serving = serving or Serving()
    accounts = Accounts(app_core.store, app_core.settings.data_dir)
    grants: Any = None  # the OAuth side of a team server; set below when it has one
    shop = Running()  # the demo shop, started only if someone asks to scan it

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if owned:
            await app_core.start()
        yield
        await shop.stop()
        if owned:
            await app_core.stop()

    # The interactive API reference sits under /v1 with the API. /docs belongs to the site's own documentation.
    api = FastAPI(title="Catchbunny", version="0.1.0", lifespan=lifespan, docs_url="/v1/docs", redoc_url=None, openapi_url="/v1/openapi.json")
    store = app_core.store
    if serving.team:
        from .oauth import Grants

        grants = Grants(store, accounts, serving.public_url)

    async def bearer(token: str) -> User | None:
        return await accounts.token_user(token) or (await grants.access_user(token) if grants else None)

    api.add_middleware(Guard, serving=serving, accounts=accounts, bearer=bearer)
    # Other origins may read this API only where no browser credential is involved and the server is not
    # one a stray web page could reach: the console is same-origin, and the dev server proxies.
    if not serving.team and not serving.local_only:
        api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], expose_headers=["*"])
    api.state.accounts, api.state.grants, api.state.serving = accounts, grants, serving

    @api.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        code = {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND", 409: "CONFLICT", 422: "INVALID", 429: "RATE_LIMITED"}.get(exc.status_code, "ERROR")
        return JSONResponse({"status": "error", "code": code, "message": exc.detail, "retryable": exc.status_code >= 500 or exc.status_code == 429}, status_code=exc.status_code)

    @api.exception_handler(AccountError)
    async def _account_error(_: Request, exc: AccountError) -> JSONResponse:
        return await _http_error(_, HTTPException(exc.status, exc.message))

    def me(request: Request) -> User:
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(404, "This server has no accounts: it is not being served for a team.")
        return user

    def owner(request: Request) -> User:
        user = me(request)
        if user.role != "owner":
            raise HTTPException(403, "Only the owner can do that.")
        return user

    async def welcome(response: Response, user: User) -> dict[str, Any]:
        """Sign this browser in as `user`."""
        secret = await accounts.open_session(user)
        response.set_cookie(COOKIE, secret, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=serving.secure, path="/")
        return user.public()

    async def need(kind: str, id: str) -> dict[str, Any]:
        doc = await store.get(kind, id)
        if doc is None:
            raise HTTPException(404, f"{kind} {id} not found")
        return doc

    async def project_of(id: str) -> Project:
        return app_core.load_project(await need("project", id))

    # ------------------------------------------------------------------ meta

    @api.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "pool": app_core.pool.stats()}

    @api.get("/v1/meta")
    async def meta(request: Request) -> dict[str, Any]:
        """What this server is and how it is being offered. The site asks this to learn whether a
        console is here at all: a copy of the site on a static host has no server to answer."""
        user = getattr(request.state, "user", None)
        base = serving.public_url.rstrip("/") or str(request.base_url).rstrip("/")
        return {
            "product": "catchbunny",
            "version": package_version("catchbunny"),
            "mode": "team" if serving.team else "local",
            "needs_owner": serving.team and await accounts.needs_owner(),
            "user": user.public() if user else None,
            "team": (await accounts.team()).name if serving.team and user else "",
            "url": base,
            "mcp_url": f"{base}/mcp",
            "signs_connectors_in": serving.signs_connectors_in,
            "browsers": list(BROWSERS),
        }

    # ------------------------------------------------------------------ accounts (a team server)

    @api.post("/v1/auth/setup", status_code=201)
    async def setup(body: OwnerIn, request: Request, response: Response) -> dict[str, Any]:
        """Create the owner. Needs the setup code printed where the server was started."""
        if not serving.team:
            raise HTTPException(404, "This server has no accounts: it is not being served for a team.")
        return await welcome(response, await accounts.create_owner(body.code, email=body.email, name=body.name, password=body.password, client=_client(request)))

    @api.post("/v1/auth/join", status_code=201)
    async def join(body: OwnerIn, request: Request, response: Response) -> dict[str, Any]:
        """Create a member account. Needs the team's code, which the owner hands out."""
        if not serving.team:
            raise HTTPException(404, "This server has no accounts: it is not being served for a team.")
        return await welcome(response, await accounts.join(body.code, email=body.email, name=body.name, password=body.password, client=_client(request)))

    @api.post("/v1/auth/sign-in")
    async def sign_in(body: SignInIn, request: Request, response: Response) -> dict[str, Any]:
        if not serving.team:
            raise HTTPException(404, "This server has no accounts: it is not being served for a team.")
        return await welcome(response, await accounts.sign_in(body.email, body.password, client=_client(request)))

    @api.post("/v1/auth/sign-out", status_code=204)
    async def sign_out(request: Request, response: Response) -> None:
        await accounts.close_session(request.cookies.get(COOKIE, ""))
        response.delete_cookie(COOKIE, path="/")

    @api.get("/v1/team")
    async def team(request: Request) -> dict[str, Any]:
        user = me(request)
        info = await accounts.team()
        members = [{**u.public(), "you": u.id == user.id} for u in await accounts.users()]
        return {"name": info.name, "members": members, "code": info.code if user.role == "owner" else ""}

    @api.patch("/v1/team")
    async def rename_team(body: TeamPatch, request: Request) -> dict[str, Any]:
        owner(request)
        return {"name": (await accounts.rename_team(body.name)).name}

    @api.post("/v1/team/code")
    async def new_team_code(request: Request) -> dict[str, Any]:
        """Replace the team code. Whoever already joined stays; the old code stops working."""
        owner(request)
        return {"code": (await accounts.new_team_code()).code}

    @api.delete("/v1/team/members/{user_id}", status_code=204)
    async def remove_member(user_id: str, request: Request) -> None:
        await accounts.remove(user_id, owner(request))

    @api.get("/v1/tokens")
    async def list_tokens(request: Request) -> list[dict[str, Any]]:
        return [t.model_dump() for t in await accounts.tokens(me(request))]

    @api.post("/v1/tokens", status_code=201)
    async def create_token(body: TokenIn, request: Request) -> dict[str, Any]:
        """A key for a coding agent or a CI job. The secret is in this answer and nowhere else, ever."""
        token, secret = await accounts.create_token(me(request), body.label)
        return {**token.model_dump(), "secret": secret}

    @api.delete("/v1/tokens/{token_id}", status_code=204)
    async def revoke_token(token_id: str, request: Request) -> None:
        await accounts.revoke_token(token_id, me(request))

    @api.get("/v1/oauth/requests/{request_id}")
    async def connector_request(request_id: str, request: Request) -> dict[str, Any]:
        """A connector waiting for this person's answer: who it says it is, and where the answer goes."""
        me(request)
        pending = await grants.pending(request_id) if grants else None
        if pending is None:
            raise HTTPException(404, "This request has expired or was already answered. Start again from the app you are connecting.")
        return pending

    @api.post("/v1/oauth/requests/{request_id}")
    async def answer_connector(request_id: str, body: AnswerIn, request: Request) -> dict[str, Any]:
        where = await grants.answer(request_id, me(request), body.approve) if grants else None
        if where is None:
            raise HTTPException(404, "This request has expired or was already answered. Start again from the app you are connecting.")
        return {"redirect_to": where}

    @api.get("/v1/models")
    async def models() -> dict[str, Any]:
        """The brains this server can run right now, and why not for the rest."""
        s = app_core.settings
        no_claude = await app_core.gateway("claude").provider.unavailable()
        local, no_ollama = await app_core.gateway("ollama").provider.installed()  # type: ignore[attr-defined]
        return {
            "auto": "heuristic" if no_claude else "claude",  # what brain=auto resolves to
            "providers": [
                {"id": "heuristic", "available": True, "reason": "", "models": [], "default_model": ""},
                {"id": "claude", "available": not no_claude, "reason": no_claude, "models": [{"name": s.model}], "default_model": s.model},
                {"id": "ollama", "available": not no_ollama, "reason": no_ollama, "models": local, "default_model": s.ollama_model},
            ],
        }

    # ------------------------------------------------------------------ projects

    @api.post("/v1/projects", status_code=201)
    async def create_project(body: ProjectIn) -> dict[str, Any]:
        project = Project(
            name=body.name,
            environments=[Environment(base_url=body.base_url, login=body.login, reset_path=body.reset_path)],
            personas=body.personas,
            knowledge=body.knowledge,
            policy=body.policy,
        )
        await app_core.save_project(project)
        return project.public()

    async def demo_project() -> Project:
        """The demo shop as an app under test, with the shop running. It runs only while this server
        does, so it is started again whenever it is about to be scanned."""
        try:
            url = await shop.start()
        except (OSError, RuntimeError) as exc:
            raise HTTPException(503, str(exc)) from exc
        existing = next((d for d in await store.list("project") if d["name"] == DEMO_NAME), None)
        if existing:
            project = app_core.load_project(existing)
            project.environments[0].base_url = url
            await app_core.save_project(project)
            return project
        project = await ensure_project(app_core, url, user=USER, password=PASSWORD)
        project.name = DEMO_NAME
        await app_core.save_project(project)
        return project

    @api.post("/v1/demo", status_code=202)
    async def scan_the_demo_shop() -> dict[str, Any]:
        """Start Mug & Co, a shop with bugs planted in it, beside this server, and scan it: something to
        watch before there is an app of your own to point Catchbunny at. No model, so nothing is billed."""
        project = await demo_project()
        spec = RunSpec(kind=RunKind.DEEP_SCAN, brain="heuristic", testers=6, devices=["desktop", "iphone"], max_steps=500, max_minutes=5)
        run = await app_core.runs.start(project, spec)
        return {"project_id": project.id, "run_id": run.id, "url": project.environments[0].base_url, "planted": len(BUGS)}

    @api.get("/v1/projects")
    async def list_projects() -> list[dict[str, Any]]:
        return [app_core.load_project(d).public() for d in await store.list("project")]

    @api.get("/v1/projects/{project_id}")
    async def get_project(project_id: str) -> dict[str, Any]:
        return (await project_of(project_id)).public()

    @api.patch("/v1/projects/{project_id}")
    async def patch_project(project_id: str, body: ProjectPatch) -> dict[str, Any]:
        project = await project_of(project_id)
        if body.base_url is not None:
            project.environments[0].base_url = body.base_url
        if body.login is not None:
            project.environments[0].login = body.login
        if body.reset_path is not None:
            project.environments[0].reset_path = body.reset_path
        for name in ("personas", "knowledge", "policy"):
            if (value := getattr(body, name)) is not None:
                setattr(project, name, value)
        await app_core.save_project(project)
        return project.public()

    @api.get("/v1/projects/{project_id}/map")
    async def get_map(project_id: str) -> dict[str, Any]:
        project = await project_of(project_id)
        saved = await store.get("pmap", f"{project.id}:{project.environments[0].name}")
        if saved is None:
            return {"states": [], "edges": {}, "coverage": {}}
        done, items = saved.get("done", {}), saved.get("items", {})
        states = [{**s, "controls_total": len(items.get(s["id"], [])), "controls_done": len(done.get(s["id"], []))} for s in saved["states"]]
        return {"origin": saved["origin"], "states": states, "edges": saved["edges"]}

    # ------------------------------------------------------------------ scenarios

    @api.post("/v1/projects/{project_id}/scenarios", status_code=201)
    async def create_scenario(project_id: str, body: ScenarioIn) -> dict[str, Any]:
        await project_of(project_id)
        try:
            scenario = scenario_from_yaml(body.yaml, project_id)
        except Exception as exc:
            raise HTTPException(422, f"invalid scenario: {exc}") from exc
        await store.put_now("scenario", scenario.id, scenario, parent=project_id, created=scenario.created_at)
        return scenario.model_dump(mode="json")

    @api.get("/v1/projects/{project_id}/scenarios")
    async def list_scenarios(project_id: str) -> list[dict[str, Any]]:
        return await store.list("scenario", parent=project_id)

    @api.get("/v1/scenarios/{scenario_id}")
    async def get_scenario(scenario_id: str, format: Literal["json", "yaml"] = "json") -> Any:
        scenario = Scenario(**await need("scenario", scenario_id))
        return PlainTextResponse(scenario_to_yaml(scenario), media_type="application/yaml") if format == "yaml" else scenario.model_dump(mode="json")

    @api.delete("/v1/scenarios/{scenario_id}", status_code=204)
    async def delete_scenario(scenario_id: str) -> None:
        await need("scenario", scenario_id)
        await store.delete("scenario", scenario_id)

    @api.post("/v1/projects/{project_id}/relevance")
    async def scenario_relevance(project_id: str, body: RelevanceIn) -> list[dict[str, Any]]:
        scenarios = [Scenario(**d) for d in await store.list("scenario", parent=project_id)]
        return relevance.select(scenarios, body.changed_files, body.diff)

    # ------------------------------------------------------------------ runs

    @api.post("/v1/projects/{project_id}/runs", status_code=202)
    async def start_run(project_id: str, spec: RunSpec, idempotency_key: str = Header(default="")) -> dict[str, Any]:
        project = await project_of(project_id)
        if project.name == DEMO_NAME:
            project = await demo_project()
        run = await app_core.runs.start(project, spec, idempotency_key)
        return run.model_dump(mode="json")

    @api.get("/v1/projects/{project_id}/runs")
    async def list_runs(project_id: str) -> list[dict[str, Any]]:
        return list(reversed(await store.list("run", parent=project_id)))

    @api.get("/v1/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        return await need("run", run_id)

    @api.post("/v1/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict[str, Any]:
        run = Run(**await need("run", run_id))
        if run.status in TERMINAL:
            raise HTTPException(409, f"run already {run.status.value}")
        return {"canceled": await app_core.runs.cancel(run_id)}

    @api.get("/v1/runs/{run_id}/events")
    async def run_events(run_id: str, after: int = Query(default=0, ge=0), last_event_id: str = Header(default="")) -> StreamingResponse:
        await need("run", run_id)
        start = int(last_event_id) if last_event_id.isdigit() else after

        async def sse() -> AsyncIterator[str]:
            stream = app_core.bus.stream(run_id, start).__aiter__()
            pending: asyncio.Task[Any] | None = None
            try:
                while True:
                    pending = pending or asyncio.ensure_future(anext(stream))
                    done, _ = await asyncio.wait({pending}, timeout=15)
                    if not done:
                        yield ": keep-alive\n\n"  # the pending read survives; nothing is dropped
                        continue
                    try:
                        event = pending.result()
                    except StopAsyncIteration:
                        return
                    pending = None
                    # `ts` rides in the payload so a client can lay a finished run out on its real timeline.
                    yield f"id: {event.seq}\nevent: {event.type}\ndata: {json.dumps({**event.data, 'ts': event.ts}, default=str)}\n\n"
            finally:
                # A client that disconnects cancels this handler while a read is pending. Cancel the
                # read; the generator may still be unwinding from it, in which case that unwinding
                # is what unsubscribes it and an explicit close has nothing left to do.
                if pending is not None:
                    pending.cancel()
                with suppress(RuntimeError):
                    await stream.aclose()

        return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @api.get("/v1/runs/{run_id}/missions")
    async def run_missions(run_id: str) -> list[dict[str, Any]]:
        return await store.list("mission", parent=run_id)

    @api.get("/v1/missions/{mission_id}/steps")
    async def mission_steps(mission_id: str) -> list[dict[str, Any]]:
        return (await need("steps", mission_id))["steps"]

    # ------------------------------------------------------------------ findings

    @api.get("/v1/runs/{run_id}/findings")
    async def run_findings(run_id: str) -> list[dict[str, Any]]:
        return [f.model_dump(mode="json") for f in rank([Finding(**d) for d in await store.list("finding", parent=run_id)])]

    @api.get("/v1/findings/{finding_id}")
    async def get_finding(finding_id: str) -> dict[str, Any]:
        f = Finding(**await need("finding", finding_id))
        return {**f.model_dump(mode="json"), "repro_text": f.repro_text()}

    @api.get("/v1/findings/{finding_id}/export")
    async def export_finding(finding_id: str, format: Literal["scenario", "playwright", "prompt"] = "scenario") -> PlainTextResponse:
        f = Finding(**await need("finding", finding_id))
        base_url = (await project_of(f.project_id)).environments[0].base_url
        if format == "scenario":
            return PlainTextResponse(scenario_to_yaml(finding_to_scenario(f)), media_type="application/yaml")
        if format == "playwright":
            return PlainTextResponse(playwright_test(f, base_url), media_type="text/x-python")
        return PlainTextResponse(fix_prompt(f, base_url), media_type="text/markdown")

    @api.post("/v1/findings/{finding_id}/scenario", status_code=201)
    async def keep_as_scenario(finding_id: str) -> dict[str, Any]:
        f = Finding(**await need("finding", finding_id))
        scenario = finding_to_scenario(f)
        await store.put_now("scenario", scenario.id, scenario, parent=f.project_id, created=scenario.created_at)
        return scenario.model_dump(mode="json")

    @api.post("/v1/findings/{finding_id}/rerun")
    async def rerun_finding(finding_id: str, runs: int = Query(default=1, ge=1, le=5)) -> dict[str, Any]:
        f = Finding(**await need("finding", finding_id))
        return await app_core.runs.rerun_finding(await project_of(f.project_id), f, runs)

    @api.get("/v1/artifacts/{name}")
    async def artifact(name: str) -> FileResponse:
        path = app_core.artifacts.path(name)
        if path is None:
            raise HTTPException(404, "artifact not found")
        return FileResponse(path)

    # ------------------------------------------------------------------ console
    # The built web console (web/dist), when present, is served by the same process:
    # one port, no CORS, and `catchbunny start` is the whole product.

    if WEB_DIST.is_dir():

        @api.get("/{path:path}", include_in_schema=False)
        async def console(path: str) -> FileResponse:
            if path.startswith("v1/"):
                raise HTTPException(404, "no such endpoint")  # an API typo must not be answered with a web page
            file = (WEB_DIST / path).resolve()
            if file.is_relative_to(WEB_DIST) and file.is_file():
                return FileResponse(file)
            if path.startswith("assets/") or Path(path).suffix:
                # A file that is not there, usually a script from before the last build that an open tab
                # still asks for. Answering with the page would hand a browser HTML where it expects code.
                raise HTTPException(404, "no such file")
            return FileResponse(WEB_DIST / "index.html", headers={"Cache-Control": "no-cache"})  # unknown paths are client-side routes

    return api


class Front:
    """One port for everything: the MCP endpoint (and, on a team server, its OAuth routes) beside the API and the site."""

    def __init__(self, api: FastAPI, mcp: Any) -> None:
        self.api, self.mcp = api, mcp

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            async with self.mcp.router.lifespan_context(self.mcp):  # the MCP transport's own startup and shutdown
                await self.api(scope, receive, send)
            return
        path = scope.get("path", "")
        await (self.mcp if path.rstrip("/") in MCP_PATHS or path.startswith("/.well-known/oauth-") else self.api)(scope, receive, send)


def serve_app(core: App, serving: Serving) -> Front:
    """What `catchbunny start` runs."""
    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
    from mcp.server.transport_security import TransportSecuritySettings
    from pydantic import AnyHttpUrl

    from .mcp_server import HTTP_MAX_WAIT, create_mcp

    api = create_app(core, manage=True, serving=serving)
    grants, public = api.state.grants, serving.public_url.rstrip("/")
    if not serving.team:
        # No accounts, so the endpoint answers only to localhost, exactly like the API beside it.
        return Front(api, create_mcp(core, max_wait=HTTP_MAX_WAIT).streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True, host="127.0.0.1"))
    if serving.signs_connectors_in:
        auth = AuthSettings(
            issuer_url=public,  # type: ignore[arg-type]  # as a string, so no slash is added: an issuer is compared character by character
            resource_server_url=f"{public}/mcp",  # type: ignore[arg-type]
            validate_token_resource=False,  # every token this server issues is for this server; there is no other resource
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        )
        server = create_mcp(core, max_wait=HTTP_MAX_WAIT, auth=auth, auth_server_provider=grants)
    else:  # no HTTPS, so no sign-in dance: keys only
        auth = AuthSettings(issuer_url=AnyHttpUrl("http://localhost"), resource_server_url=None)
        server = create_mcp(core, max_wait=HTTP_MAX_WAIT, auth=auth, token_verifier=_Keys(grants))
    open_to_any_host = TransportSecuritySettings(enable_dns_rebinding_protection=False)  # the caller proves itself with a token instead
    return Front(api, server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True, transport_security=open_to_any_host, host="127.0.0.1"))


class _Keys:
    """Checks a bearer token for an MCP endpoint that takes keys but has no OAuth routes."""

    def __init__(self, grants: Any) -> None:
        self.grants = grants

    async def verify_token(self, token: str) -> Any:
        return await self.grants.load_access_token(token)
