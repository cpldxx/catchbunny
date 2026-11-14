"""`catchbunny` command line. Three commands:

    catchbunny start                         # the console in your browser, the API and the MCP endpoint, on this machine
    catchbunny scan http://localhost:3000    # scan an app from the terminal or CI, without the console
    catchbunny connect                       # point Claude Code and Codex at the MCP endpoint `start` serves

`start --team --public-url https://qa.example.com` serves the same for a team, with sign-in.
Older commands (serve, mcp, doctor, discover, run, export, scenarios, demo) still work
and are left out of the help.

`scan` exits 0 = passed, 1 = failed (CI gate), 2 = blocked / error. With `--json`
the last line of stdout is one machine-readable object for a coding agent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import Settings
from .domain import BROWSERS, Finding, Policy, Project, Run, RunKind, RunSpec, RunStatus, Scenario
from .engine.orchestrator import App
from .engine.scenarios import finding_to_scenario, fix_prompt, playwright_test, scenario_from_yaml, scenario_to_yaml
from .engine.triage import rank
from .projects import ensure_project

EXIT = {RunStatus.PASSED: 0, RunStatus.FAILED: 1}


def _say(*parts: Any) -> None:
    print(*parts, file=sys.stderr, flush=True)


async def _project(app: App, args: argparse.Namespace) -> Project:
    return await ensure_project(
        app, args.url, user=args.user, password=args.password, login_path=args.login_path, policy=Policy(args.policy), knowledge=getattr(args, "knowledge", ""),
        reset_path=args.reset_path or None,
    )


async def _follow(app: App, run: Run, quiet: bool) -> None:
    async for event in app.bus.stream(run.id):
        d = event.data
        if quiet:
            continue
        if event.type == "run.phase":
            _say(f"== {d['phase']}")
        elif event.type == "mission.finished":
            m = d["mission"]
            _say(f"   mission done  {m['title'][:60]:<60} {m['steps_taken']:>3} steps  {m.get('verdict') or ''}")
        elif event.type == "finding":
            f = d["finding"]
            v = f["verification"]
            _say(f"   {f['status']:<15} {f['severity']}  {f['title'][:80]}  ({v['reproduced']}/{v['runs']} replays, {len(f['repro'])} steps)")
        elif event.type == "scenario.finished":
            _say(f"   {d['verdict']:<13} {d['name'][:80]}  [{d['learned_steps']} learned, {d['model_steps']} model]  {d['reason']}")
        elif event.type == "scenario.drafted":
            _say(f"   drafted scenario: {d['name']}")
        elif event.type == "run.warning":
            _say(f"   warning: {d['message']}")


async def _run(app: App, project: Project, spec: RunSpec, args: argparse.Namespace) -> int:
    run = await app.runs.start(project, spec)
    await _follow(app, run, args.json)
    return await _report(app, await app.runs.wait(run.id), args.json)


async def _report(app: App, run: Run, as_json: bool) -> int:
    """A finished run, said once: one JSON object for an agent, or a summary for a person. Returns the exit code."""
    findings = rank([Finding(**d) for d in await app.store.list("finding", parent=run.id)])
    if as_json:
        nxt = [{"argv": ["catchbunny", "export", f.id, "--format", "prompt"]} for f in findings if f.status.value == "confirmed"][:5]
        print(json.dumps({"status": run.status.value, "run_id": run.id, "error": run.error, "summary": run.summary, "findings": [{**f.model_dump(mode="json", include={"id", "severity", "status", "title", "url"}), "steps": f.repro_text()} for f in findings], "next": nxt}))
    else:
        _say(f"\n{run.status.value.upper()}  run {run.id}" + (f"  - {run.error}" if run.error else ""))
        s = run.summary
        if "coverage" in s:
            c, fs, b = s["coverage"], s["findings"], s["budget"]
            _say(f"  screens {c['screens']}  controls {c['controls_exercised']}/{c['controls_found']}  steps {b['steps']}  {s['steps_per_second']} steps/s  hunt {s['hunt_seconds']}s + triage {s['triage_seconds']}s")
            _say(f"  findings: {fs['confirmed']} confirmed, {fs['flaky']} flaky, {fs['not_reproduced']} not reproduced, {fs['unverified']} unverified   repro steps {fs['repro_steps_before']} -> {fs['repro_steps_after']} after minimisation")
            if b["llm"]["requests"]:
                _say(f"  model: {b['llm']['requests']} requests, ${b['llm']['usd']}, cache hit rate {b['llm']['cache_hit_rate']}")
        for f in findings:
            if f.status.value in ("confirmed", "flaky", "unverified"):
                _say(f"\n  [{f.severity.value}] {f.title}   ({f.status.value}, {f.id})")
                for i, line in enumerate(f.repro_text(), 1):
                    _say(f"      {i}. {line}")
        if "scenarios" in s:
            _say(f"  scenarios: {s['passed']} passed, {s['failed']} failed, {s['blocked']} blocked, {s['inconclusive']} inconclusive; {s['steps_replayed_without_model']} steps replayed without a model")
    return EXIT.get(run.status, 2)


LOOPBACK = ("127.0.0.1", "localhost", "::1")


async def _browser_starts(settings: Settings, engine: str = "chromium") -> str:
    """Empty if this engine can be launched here, else why not. This is the one thing a fresh install is usually missing."""
    from playwright.async_api import async_playwright

    try:
        async with async_playwright() as pw:
            await (await getattr(pw, engine).launch(headless=settings.headless)).close()
    except Exception as exc:
        lines = [line.strip(" -") for line in str(exc).strip().splitlines() if line.strip()]
        said = next((line.split("[err]", 1)[1].strip() for line in reversed(lines) if "[err]" in line and "headless mode" not in line), "")  # what the browser itself printed
        return said or lines[0]
    return ""


def _missing(problem: str) -> bool:
    """Whether a launch failed because the browser was never downloaded, rather than for some other reason."""
    return "Executable doesn't exist" in problem or "playwright install" in problem


def _install_browser(settings: Settings, engine: str) -> None:
    # Headless Chromium needs only its headless shell, about half the download of the whole browser.
    shell = ["--only-shell"] if engine == "chromium" and settings.headless else []
    subprocess.run([sys.executable, "-m", "playwright", "install", engine, *shell], check=False, stdout=sys.stderr)


async def _ensure_browser(settings: Settings, engine: str) -> str:
    """Download the engine the first time it is needed, so a new install works on its first command.
    Empty when the browser starts, else why it does not."""
    problem = await _browser_starts(settings, engine)
    if problem and _missing(problem):
        _say(f"First run: downloading the {engine} browser that Catchbunny tests with (once, about 100 MB).")
        _install_browser(settings, engine)
        problem = await _browser_starts(settings, engine)
    return problem


async def _demo(args: argparse.Namespace) -> int:
    import uvicorn

    from .demo_shop import BUGS, PASSWORD, USER, create_shop, parse_bugs

    bugs = parse_bugs(args.bugs)
    url = f"http://127.0.0.1:{args.port}"
    _say(f"Mug & Co, the demo shop      {url}   ({len(bugs)} of {len(BUGS)} bugs planted)")
    _say("  In another terminal, point Catchbunny at it:")
    _say(f"    catchbunny scan {url} --user {USER} --password {PASSWORD}")
    _say("  Stop it with Ctrl+C.")
    config = uvicorn.Config(create_shop(bugs), host="127.0.0.1", port=args.port, log_level="warning")
    await uvicorn.Server(config).serve()
    return 0


async def _doctor(settings: Settings, install: bool, engines: list[str] | None = None) -> int:
    """Whether this machine can run a scan. Chromium is what a scan uses unless told otherwise, so it is
    the one that decides the exit code; the other engines are reported when asked for."""
    from importlib.metadata import version

    from .brain.claude import llm_credentials_present

    _say(f"catchbunny {version('catchbunny')}, Python {sys.version.split()[0]}")
    wanted = engines or ["chromium"]
    problems: dict[str, str] = {}
    for engine in wanted:
        problem = await _browser_starts(settings, engine)
        if problem and install:
            _say(f"{engine:<16}missing; installing it for Playwright (once, about 100 MB)")
            _install_browser(settings, engine)
            problem = await _browser_starts(settings, engine)
        if problem:
            problems[engine] = problem
        _say(f"{engine:<16}" + (f"cannot start: {problem}\n                fix: {sys.executable} -m playwright install {engine}" if problem else "ok"))
    problem = problems.get("chromium", "") if "chromium" in wanted else next(iter(problems.values()), "")
    there = next(p for p in [settings.data_dir.absolute(), *settings.data_dir.absolute().parents] if p.exists())  # a check should leave nothing behind
    writable = "" if os.access(there, os.W_OK) else f"{there} is not writable"
    _say(f"data directory  {settings.data_dir} " + (f"cannot be used: {writable}" if writable else "ok" if settings.data_dir.exists() else "ok (made on first use)"))
    _say("model           " + ("Claude (a key is set)" if llm_credentials_present() else "none set: scans use the detectors, which need no model. Add ANTHROPIC_API_KEY or use --brain ollama for goal-directed testers"))
    return 1 if problem or writable else 0


async def _serve_mcp_http(settings: Settings, args: argparse.Namespace) -> int:
    import uvicorn

    from .mcp_server import HTTP_MAX_WAIT, access_token, create_mcp, http_app

    token = args.token or os.environ.get("CATCHBUNNY_MCP_TOKEN", "")
    if token == "auto":
        token = access_token(settings.data_dir)
    if not token and args.host not in LOOPBACK:
        _say(f"Refusing to serve {args.host} without an access token: whoever reaches this server can make this machine open any address it can reach.\nAdd --token auto (or set CATCHBUNNY_MCP_TOKEN).")
        return 2
    local = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
    _say(f"Catchbunny MCP over HTTP   {local}/mcp")
    if token:
        _say(f"  with a header          Authorization: Bearer {token}")
        _say(f"  or as one URL          {local}/{token}/mcp     (for a connector that takes only a URL)")
        _say("  From another machine, put a tunnel or a host with HTTPS in front and use its address in place of the one above, e.g.")
        _say(f"    cloudflared tunnel --url {local}")
        _say("  Scans run from this machine: `localhost` in a scan is this machine. Treat the token like a password.")
    else:
        _say("  No token: only this machine can use it. Add --token auto to connect from elsewhere.")
    async with App(settings) as core:  # one engine for every request; the MCP layer on top keeps no session
        config = uvicorn.Config(http_app(create_mcp(core, max_wait=HTTP_MAX_WAIT), token=token), host=args.host, port=args.port, log_level="warning")
        await uvicorn.Server(config).serve()
    return 0


def _can_open_browser() -> bool:
    """Whether opening a page will put it in front of the person who typed the command: not in a script,
    and not on a machine with no screen, where Linux may hand the page to a text browser in this terminal."""
    if not sys.stderr.isatty():
        return False
    return sys.platform in ("darwin", "win32") or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _catchbunny_at(base: str) -> bool:
    """Whether a Catchbunny server answers at `base`."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"{base}/v1/meta", timeout=2) as reply:
            return json.loads(reply.read()).get("product") == "catchbunny"
    except (OSError, ValueError):
        return False


async def _start(settings: Settings, args: argparse.Namespace, open_browser: bool) -> int:
    """The site, the console, the API and the MCP endpoint on one port: for this machine, or for a team."""
    import logging
    import webbrowser

    import uvicorn

    from .accounts import Accounts
    from .api import WEB_DIST, Serving, serve_app

    team = args.team or os.environ.get("CATCHBUNNY_TEAM", "") == "1"
    settings.guard_network = settings.guard_network or team  # people other than this machine's owner start scans: see browser/netguard.py
    local = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
    public = (args.public_url or os.environ.get("CATCHBUNNY_PUBLIC_URL", "") or local).rstrip("/")
    open_browser = open_browser and not team and _can_open_browser()
    if not team and args.host not in LOOPBACK:
        _say(
            f"Refusing to serve {args.host} without accounts: whoever reaches this server can make this machine open any address it can reach.\n"
            "Serve it for a team instead, so people sign in:\n"
            f"  catchbunny start --team --host {args.host} --public-url https://your.domain"
        )
        return 2
    try:  # say it plainly now, rather than let the server start, fail to bind and stop again
        with socket.socket(socket.AF_INET6 if ":" in args.host else socket.AF_INET) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((args.host, args.port))
    except OSError:
        if not team and _catchbunny_at(local):
            _say(f"Catchbunny is already running at {local}/app")
            if open_browser:
                webbrowser.open(f"{local}/app")
            return 0
        _say(f"Port {args.port} is in use by another program. Start Catchbunny on another one: catchbunny start --port {args.port + 1}")
        return 2
    if problem := await _ensure_browser(settings, settings.browser):
        _say(f"Warning: the {settings.browser} browser cannot start ({problem}). The console will open, but scans will not. Check with: catchbunny doctor")
    logging.getLogger("mcp").setLevel(logging.WARNING)  # the MCP transport's start-up chatter is not for this terminal
    serving = Serving(team=team, public_url=public, local_only=not team)
    core = App(settings)
    if not WEB_DIST.is_dir():
        _say("The console is not built yet, so only the API and the MCP endpoint are served. Build it once: (cd web && npm install && npm run build)")
    if team:
        _say(f"Catchbunny for a team      {public}")
        _say(f"  console                  {public}/app")
        _say(f"  MCP endpoint for agents  {public}/mcp   (a key from the console, or sign in from Claude or ChatGPT)")
        if not serving.signs_connectors_in:
            _say("  Connectors that sign people in (Claude, ChatGPT) need HTTPS. Put a proxy with a certificate in front and pass --public-url https://your.domain")
        if public == local and args.host not in LOOPBACK:
            _say("  No --public-url was given, so links use this machine's address. Pass the address people will actually type.")
        await core.store.open()  # only to see whether the owner exists yet; the server opens it again on start
        try:
            first = await Accounts(core.store, settings.data_dir).needs_owner()
        finally:
            await core.store.close()
        core = App(settings)
        if first:
            code = Accounts(core.store, settings.data_dir).setup_code()
            _say("  First time here. Create the owner account:")
            _say(f"    {public}/app/setup?code={code}")
    else:
        _say(f"Catchbunny is running      {local}/app")
        _say(f"  MCP endpoint for agents  {local}/mcp   (connect them with: catchbunny connect)")
        _say("  Leave this terminal open. Ctrl+C stops it.")
    config = uvicorn.Config(serve_app(core, serving), host=args.host, port=args.port, log_level="warning", proxy_headers=True, forwarded_allow_ips=args.trust_proxy)
    server = uvicorn.Server(config)

    async def show() -> None:
        while not server.started:
            if server.should_exit:
                return
            await asyncio.sleep(0.05)
        webbrowser.open(f"{local}/app")

    opener = asyncio.create_task(show()) if open_browser else None
    await server.serve()
    if opener:
        opener.cancel()
    return 0


async def _main(args: argparse.Namespace) -> int:
    settings = Settings()
    if args.data_dir:
        settings.data_dir = Path(args.data_dir)
    if args.command == "doctor":
        return await _doctor(settings, install=args.install_browser, engines=list(BROWSERS) if args.browser == "all" else [args.browser])
    if args.command == "connect":
        from .connect import connect

        url = None if args.stdio else args.url
        code = connect(args.clients, url=url, scope=args.scope, remove=args.remove, dry=args.print, say=_say)
        if code == 0 and not args.remove and not args.print:
            if url is None:  # the agent will start Catchbunny itself, so the browser must be here for it
                if not args.no_browser and await _browser_starts(settings):
                    code = await _doctor(settings, install=True) or code
            elif not _catchbunny_at(url.removesuffix("/mcp")):
                _say("\nCatchbunny is not running yet. Start it, and leave it running while the agent works:\n  catchbunny start")
        return code
    if args.command in ("start", "serve"):
        return await _start(settings, args, open_browser=args.command == "start" and not args.no_open)
    if args.command == "demo":
        return await _demo(args)
    if args.command in ("scan", "discover", "run") and (problem := await _ensure_browser(settings, args.browser)):
        _say(f"The {args.browser} browser cannot start: {problem}\nCheck with: catchbunny doctor --browser {args.browser}")
        return 2
    if args.command == "mcp":
        if args.http:
            return await _serve_mcp_http(settings, args)
        from .mcp_server import create_mcp

        await create_mcp(App(settings), manage=True).run_stdio_async()  # stdout belongs to the protocol from here on
        return 0
    async with App(settings) as app:
        if args.command in ("scan", "discover"):
            project = await _project(app, args)
            spec = RunSpec(
                kind=RunKind.DEEP_SCAN if args.command == "scan" else RunKind.DISCOVERY,
                goal=args.goal,
                areas=args.area,
                devices=args.devices.split(","),
                browser=args.browser,
                testers=args.testers,
                max_steps=args.steps,
                max_minutes=args.minutes,
                max_usd=args.usd,
                brain=args.brain,
                model=args.model,
                verify_runs=args.verify_runs,
                minimize=not args.no_minimize,
                screenshots=args.screenshots,
                record=not args.no_record,
            )
            return await _run(app, project, spec, args)
        if args.command == "run":
            project = await _project(app, args)
            ids = []
            for file in args.files:
                scenario = scenario_from_yaml(Path(file).read_text(), project.id)
                await app.store.put_now("scenario", scenario.id, scenario, parent=project.id, created=scenario.created_at)
                ids.append(scenario.id)
            spec = RunSpec(kind=RunKind.SCENARIOS, scenario_ids=ids, testers=args.testers, max_minutes=args.minutes, brain=args.brain, model=args.model, browser=args.browser)
            code = await _run(app, project, spec, args)
            for sid in ids:  # files are the source of truth; do not accumulate copies
                await app.store.delete("scenario", sid)
            return code
        if args.command == "export":
            doc = await app.store.get("finding", args.finding_id)
            if doc is None:
                _say(f"finding {args.finding_id} not found")
                return 2
            f = Finding(**doc)
            project = app.load_project(await app.store.get("project", f.project_id) or {})
            base = project.environments[0].base_url
            text = {"scenario": lambda: scenario_to_yaml(finding_to_scenario(f)), "playwright": lambda: playwright_test(f, base), "prompt": lambda: fix_prompt(f, base)}[args.format]()
            if args.output:
                Path(args.output).write_text(text)
                _say(f"wrote {args.output}")
            else:
                print(text)
            return 0
        if args.command == "scenarios":
            for d in await app.store.list("scenario"):
                s = Scenario(**d)
                print(f"{s.id}  [{','.join(s.tags)}]  {s.name}")
            return 0
    return 2


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="catchbunny",
        usage="catchbunny <command> [options]",
        description="Browser testers that prove a bug before they report it.",
        epilog="Start here:  catchbunny start\nEach command:  catchbunny <command> --help",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    hidden = argparse.SUPPRESS
    p.add_argument("--data-dir", default="", help=hidden)  # where runs, findings and recordings are kept (default ~/.catchbunny)
    sub = p.add_subparsers(dest="command", title="commands", metavar="<command>", prog="catchbunny")

    def server(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--port", type=int, default=8700, help="the port to serve on (default 8700)")
        sp.add_argument("--team", action="store_true", help="serve it for a team: people sign in, and join with the team's code (or set CATCHBUNNY_TEAM=1)")
        sp.add_argument("--public-url", default="", help="with --team: the address people and agents reach it at, e.g. https://qa.example.com")
        sp.add_argument("--host", default="127.0.0.1", help="the address to listen on; anything but localhost needs --team")
        sp.add_argument("--trust-proxy", default="127.0.0.1", help="addresses of the reverse proxy whose forwarded headers are believed (default 127.0.0.1)")

    def target(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--user", default="", help="a test account, to test signed in as well")
        sp.add_argument("--password", default="")
        sp.add_argument("--login-path", default="/login", help="where the sign-in page is (default /login)")
        sp.add_argument("--policy", default="safe_writes", choices=[x.value for x in Policy], help="read_only: never change anything (default safe_writes)")
        sp.add_argument("--testers", type=int, default=4, help="browsers working at once (default 4)")
        sp.add_argument("--minutes", type=float, default=5.0, help="time limit (default 5)")
        sp.add_argument("--browser", default=Settings().browser, choices=BROWSERS, help="the engine to test in; webkit is the engine Safari is built on")
        sp.add_argument("--brain", default="auto", choices=["auto", "heuristic", "claude", "ollama"], help="auto: Claude when ANTHROPIC_API_KEY is set, else no model")
        sp.add_argument("--model", default="", help=hidden)
        sp.add_argument("--reset-path", default="", help="a path on the app that puts its test data back; called before every replay")
        sp.add_argument("--json", action="store_true", help="one JSON object on stdout, for a coding agent")

    def hunt(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("url", help="the address of a running app, e.g. http://localhost:3000")
        sp.add_argument("--devices", default="desktop", help="any of desktop,laptop,tablet,iphone,android (default desktop)")
        sp.add_argument("--area", action="append", default=[], help="a path prefix to stay within (repeatable)")
        sp.add_argument("--goal", default="", help="what model-driven testers should look into")
        sp.add_argument("--steps", type=int, default=400, help=hidden)
        sp.add_argument("--usd", type=float, default=2.0, help=hidden)
        sp.add_argument("--verify-runs", type=int, default=2, help=hidden)
        sp.add_argument("--no-minimize", action="store_true", help=hidden)
        sp.add_argument("--no-record", action="store_true", help=hidden)
        sp.add_argument("--screenshots", default="on_signal", choices=["on_signal", "every_step", "off"], help=hidden)
        sp.add_argument("--knowledge", default="", help=hidden)
        target(sp)

    # The three commands. Only these have help, so only these are listed.
    sp = sub.add_parser("start", help="open the console in your browser, and serve MCP for agents", description="Serve the console, the API and the MCP endpoint at http://127.0.0.1:8700, and open the console.")
    server(sp)
    sp.add_argument("--no-open", action="store_true", help="do not open the browser")

    sp = sub.add_parser("scan", help="scan a running app from the terminal or CI", description="Hunt for bugs in a running app and report the ones that replayed in a fresh browser. Exit code 0 passed, 1 failed, 2 could not test.")
    hunt(sp)

    sp = sub.add_parser("connect", help="connect Claude Code and Codex over MCP", description="Point coding agents at the MCP endpoint that `catchbunny start` serves. With no names, every agent found on this machine.")
    sp.add_argument("clients", nargs="*", choices=["claude-code", "codex", "claude-desktop"], metavar="agent", help="claude-code, codex, claude-desktop (default: the ones found here)")
    sp.add_argument("--url", default="http://127.0.0.1:8700/mcp", help="the MCP endpoint (default http://127.0.0.1:8700/mcp)")
    sp.add_argument("--stdio", action="store_true", help="let the agent start Catchbunny itself instead, with no server running")
    sp.add_argument("--remove", action="store_true", help="take it out again")
    sp.add_argument("--print", action="store_true", help="show what would be written and change nothing")
    sp.add_argument("--scope", default="", choices=["", "project", "user"], help=hidden)
    sp.add_argument("--no-browser", action="store_true", help=hidden)

    # Earlier commands, kept working for scripts and agents that use them, and left out of the help.
    sp = sub.add_parser("serve")
    server(sp)
    sp = sub.add_parser("discover")
    hunt(sp)
    sp = sub.add_parser("run")
    sp.add_argument("files", nargs="+")
    sp.add_argument("--url", required=True)
    target(sp)
    sp = sub.add_parser("export")
    sp.add_argument("finding_id")
    sp.add_argument("--format", default="scenario", choices=["scenario", "playwright", "prompt"])
    sp.add_argument("-o", "--output", default="")
    sub.add_parser("scenarios")
    sp = sub.add_parser("mcp")
    sp.add_argument("--http", action="store_true")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8710)
    sp.add_argument("--token", default="")
    sp = sub.add_parser("doctor")
    sp.add_argument("--browser", default="chromium", choices=[*BROWSERS, "all"])
    sp.add_argument("--install-browser", action="store_true")
    sp = sub.add_parser("demo")
    sp.add_argument("--port", type=int, default=8811)
    sp.add_argument("--bugs", default="all")

    args = p.parse_args(argv)
    if args.command is None:
        p.print_help()
        sys.exit(0)
    try:
        sys.exit(asyncio.run(_main(args)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
