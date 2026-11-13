"""Browser fleet and one tester's browser session.

Concurrency model
-----------------
Everything a tester waits on is I/O (round trips to the browser, model calls),
so the whole swarm runs on one asyncio loop: no GIL contention, no locks around
shared state, thousands of pending awaits for free. Threads are used only where
they are the right tool: blocking SQLite and file writes go through
`asyncio.to_thread`.

Isolation is a BrowserContext (own cookies, storage, cache) - it costs a few
milliseconds and no extra process, where a Browser per tester would cost a
process launch each. Contexts in one browser still share its browser process,
so the pool caps contexts per browser and launches another process past that.
A semaphore bounds total contexts; a run can never over-subscribe the machine.

Three engines, one fleet: Chromium, Firefox and WebKit (the engine Safari is
built on) are all driven through the same Playwright API, and the pool keeps
browsers of each engine apart. A run names its engine; nothing else changes.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from ..config import Settings
from ..domain import BROWSERS, Action, Check, CheckResult, Credential, LoginRecipe, Policy, Signal, norm_text, origin_of, url_path
from ..store import ArtifactStore
from . import samples
from .locate import ElementNotFound, app_frames, resolve
from .observe import PAGE_QUIET_JS, PAGE_TRACKER_JS, SNAPSHOT_JS, Observation, merge

_IPHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
_ANDROID_UA = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"

DEVICES: dict[str, dict[str, Any]] = {
    "desktop": {"viewport": {"width": 1366, "height": 850}},
    "laptop": {"viewport": {"width": 1024, "height": 700}},
    "tablet": {"viewport": {"width": 820, "height": 1180}, "has_touch": True},
    "iphone": {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True, "user_agent": _IPHONE_UA},
    "android": {"viewport": {"width": 412, "height": 915}, "is_mobile": True, "has_touch": True, "user_agent": _ANDROID_UA},
}

_ACCOUNT_DESTRUCTIVE = re.compile(
    r"\b(delete|close|deactivate|destroy)\b.{0,20}\b(account|profile|workspace|organi[sz]ation)\b|\b(log|sign)\s?out\b|\bunsubscribe\b",
    re.I,
)
_MUTATING = re.compile(
    r"\b(delete|remove|destroy|cancel|pay|purchase|place order|buy|checkout|submit|save|create|add|update|send|post|apply|confirm|sign up|register)\b",
    re.I,
)
_IGNORED_URL = re.compile(r"favicon\.ico|/apple-touch-icon", re.I)
# How each engine words "this request was dropped because the page moved on": not a defect in any of them.
_ABORTED = re.compile(r"ERR_ABORTED|NS_BINDING_ABORTED|NS_ERROR_ABORT|cancell?ed", re.I)
_BACKGROUND_TYPES = frozenset({"websocket", "eventsource", "media", "ping"})
_LONG_REQUEST_S = 2.0  # a request older than this is long-polling, not page load
_DOM_WAIT_MAX_MS = 400  # ceiling for pages whose DOM never stops changing


def policy_violation(policy: Policy, action: Action, origin: str) -> str | None:
    """Supervisor-level guard applied to every brain, model or not."""
    el = action.target
    if action.kind == "navigate" and origin_of(urljoin(origin + "/", action.value)) != origin:
        return "navigation outside the app origin"
    if el is None:
        return None
    href = el.attrs.get("href", "")
    if el.role == "link" and href:
        if href.startswith(("mailto:", "tel:", "javascript:")):
            return "non-navigational link"
        if origin_of(urljoin(origin + "/", href)) != origin:
            return "external link"
    if policy is Policy.FULL:
        return None
    label = f"{el.name} {el.text}"
    if _ACCOUNT_DESTRUCTIVE.search(label):
        return "account-destroying or sign-out control"
    if policy is Policy.READ_ONLY:
        method = el.attrs.get("form_method", "")
        if action.kind in ("fill", "select", "check", "upload") and method != "get":
            return "read-only policy: field is not part of a GET form"
        if action.kind == "drag":
            return "read-only policy: dragging rearranges data"
        if action.kind == "click" and el.role != "link" and (method == "post" or _MUTATING.search(label)):
            return "read-only policy: mutating control"
    return None


# --------------------------------------------------------------------------- pool


@dataclass(slots=True)
class _Slot:
    browser: Browser
    active: int = 0


class EngineUnavailable(Exception):
    """A browser engine that cannot be started here; the message says how to get it."""


def profile(device: str, engine: str) -> dict[str, Any]:
    """A device's viewport and input, as this engine can emulate it."""
    out = dict(DEVICES.get(device, DEVICES["desktop"]))
    if engine == "firefox":
        out.pop("is_mobile", None)  # Firefox has no mobile mode: it gets the viewport, touch and user agent
    return out


class BrowserPool:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._pw: Playwright | None = None
        self._slots: dict[str, list[_Slot]] = {}  # engine -> its browser processes
        self._lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(settings.max_contexts)
        self.launched = 0
        self.opened = 0
        self.active = 0
        self.peak = 0

    async def start(self) -> None:
        if self._pw is None:
            self._pw = await async_playwright().start()

    async def stop(self) -> None:
        for slots in self._slots.values():
            for slot in slots:
                with suppress(Exception):
                    await slot.browser.close()
        self._slots.clear()
        if self._pw:
            await self._pw.stop()
            self._pw = None

    async def _lease(self, engine: str) -> _Slot:
        if engine not in BROWSERS:
            raise EngineUnavailable(f"'{engine}' is not a browser engine. Choose one of: {', '.join(BROWSERS)}.")
        async with self._lock:  # launching awaits, so selection + launch must be one critical section
            assert self._pw is not None, "BrowserPool.start() was not called"
            slots = self._slots[engine] = [s for s in self._slots.get(engine, []) if s.browser.is_connected()]  # drop crashed browsers
            slot = min((s for s in slots if s.active < self.s.contexts_per_browser), key=lambda s: s.active, default=None)
            if slot is None:
                try:
                    slot = _Slot(await getattr(self._pw, engine).launch(headless=self.s.headless))
                except PlaywrightError as exc:
                    reason = (str(exc).strip().splitlines() or ["it did not start"])[0][:200]
                    raise EngineUnavailable(f"The {engine} browser could not be started ({reason}). Run: catchbunny doctor --browser {engine} --install-browser") from exc
                slots.append(slot)
                self.launched += 1
            slot.active += 1
            return slot

    @asynccontextmanager
    async def context(
        self, device: str = "desktop", storage_state: dict[str, Any] | None = None, record_dir: str | None = None, browser: str = ""
    ) -> AsyncIterator[BrowserContext]:
        """Lease an isolated context in `browser` (an engine name). `record_dir` turns on video for it (evidence replays only)."""
        engine = browser or self.s.browser
        async with self._sem:
            slot = await self._lease(engine)
            self.opened += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
            ctx: BrowserContext | None = None
            shape = profile(device, engine)
            video: dict[str, Any] = {"record_video_dir": record_dir, "record_video_size": shape["viewport"]} if record_dir else {}
            try:
                ctx = await slot.browser.new_context(
                    **shape,
                    **video,
                    storage_state=storage_state,  # type: ignore[arg-type]
                    ignore_https_errors=True,
                    reduced_motion="reduce",
                    service_workers="block",
                    locale="en-US",
                )
                await ctx.add_init_script(PAGE_TRACKER_JS)
                yield ctx
            finally:
                slot.active -= 1
                self.active -= 1
                if ctx is not None:
                    with suppress(Exception):
                        await ctx.close()

    def stats(self) -> dict[str, int]:
        return {"browsers": sum(len(slots) for slots in self._slots.values()), "launched": self.launched, "contexts_opened": self.opened, "peak_contexts": self.peak}


# --------------------------------------------------------------------------- session


@dataclass(slots=True)
class Recorder:
    """Everything that happened on the wire and in the console, on one clock.

    Attached only to evidence replays, so the hot path of the swarm pays nothing.
    """

    started: float = field(default_factory=time.monotonic)
    network: list[dict[str, Any]] = field(default_factory=list)
    console: list[dict[str, Any]] = field(default_factory=list)
    LIMIT = 400

    def ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)

    def request(self, method: str, url: str, status: int | str, rtype: str) -> None:
        if len(self.network) < self.LIMIT:
            self.network.append({"t_ms": self.ms(), "method": method, "url": url, "status": status, "type": rtype})

    def log(self, level: str, text: str) -> None:
        if len(self.console) < self.LIMIT:
            self.console.append({"t_ms": self.ms(), "level": level, "text": text[:400]})


@dataclass(slots=True)
class ActResult:
    ok: bool = True
    error: str = ""
    healed: bool = False
    navigated: bool = False
    requests: int = 0
    dialogs: int = 0
    duration_ms: int = 0


class Session:
    """One page in one isolated context, with always-on defect detectors attached."""

    def __init__(
        self,
        context: BrowserContext,
        page: Page,
        *,
        base_url: str,
        device: str,
        settings: Settings,
        artifacts: ArtifactStore | None = None,
        credential: Credential | None = None,
    ) -> None:
        self.context, self.page = context, page
        self.base_url = base_url.rstrip("/")
        self.origin = origin_of(base_url)
        self.device = device
        self.s = settings
        self.artifacts = artifacts
        self.credential = credential
        self.refs_valid = False
        self.created = time.monotonic()
        self._pending: list[Signal] = []
        self._seen: set[str] = set()
        self._inflight: dict[Any, float] = {}
        self._requests = 0
        self._dialogs = 0
        self._tasks: set[asyncio.Task[Any]] = set()
        self._own_navigation = False
        self.recorder: Recorder | None = None
        page.on("request", self._on_request)
        page.on("requestfinished", lambda r: self._inflight.pop(r, None))
        page.on("requestfailed", self._on_request_failed)
        page.on("response", self._on_response)
        page.on("console", self._on_console)
        page.on("pageerror", self._on_page_error)
        page.on("dialog", self._on_dialog)
        context.on("page", lambda p: self._spawn(p.close()) if p is not self.page else None)

    @classmethod
    async def open(cls, context: BrowserContext, **kw: Any) -> Session:
        return cls(context, await context.new_page(), **kw)

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(lambda t: (self._tasks.discard(t), t.exception() if not t.cancelled() else None))

    # ------------------------------------------------------------------ detectors

    def _push(self, signal: Signal) -> None:
        if signal.signature not in self._seen:  # first sighting per session is enough
            self._seen.add(signal.signature)
            self._pending.append(signal)

    def _on_request(self, request: Any) -> None:
        if _IGNORED_URL.search(request.url):
            return  # the browser's own favicon fetch is not the app doing something
        self._requests += 1
        if request.resource_type not in _BACKGROUND_TYPES:
            self._inflight[request] = time.monotonic()

    def _on_request_failed(self, request: Any) -> None:
        self._inflight.pop(request, None)
        failure = request.failure or ""
        if self.recorder:
            self.recorder.request(request.method, self._relative(request.url), "failed", request.resource_type)
        if _ABORTED.search(failure) or _IGNORED_URL.search(request.url):
            return  # aborted = superseded by a navigation, not a defect
        if self._own_navigation and request.resource_type == "document":
            return  # the tester's own page load failed: reported as a blocked step, not as a bug
        self._push(
            Signal(
                kind="request_failed",
                message=f"{request.method} {url_path(request.url)} failed: {failure}",
                url=self.page.url,
                detail={"method": request.method, "url": request.url, "status": failure, "resource_type": request.resource_type},
            )
        )

    def _on_response(self, response: Any) -> None:
        status = response.status
        request = response.request
        rtype = request.resource_type
        if self.recorder:
            self.recorder.request(request.method, self._relative(response.url), status, rtype)
        if status < 400 or _IGNORED_URL.search(response.url):
            return
        # 4xx other than 404 is the app saying "no" to bad input - expected behaviour.
        # A 404 image is reported by the snapshot (it knows the <img> is visible).
        if status < 500 and (status != 404 or rtype == "image"):
            return
        # A 404 for an address the tester typed itself (a screen that only exists in
        # another session, say) is not a dead link. A 404 reached by a click is.
        if status == 404 and rtype == "document" and self._own_navigation:
            return
        self._push(
            Signal(
                kind="http_error",
                message=f"{request.method} {url_path(response.url)} returned {status}",
                url=self.page.url,
                detail={"method": request.method, "url": response.url, "status": status, "resource_type": rtype},
            )
        )

    def _on_console(self, msg: Any) -> None:
        if self.recorder:
            self.recorder.log(msg.type, msg.text)
        if msg.type != "error" or msg.text.startswith("Failed to load resource"):
            return  # resource failures are already reported, with more detail, by the network detector
        self._push(Signal(kind="console_error", message=msg.text[:300], url=self.page.url, detail={"location": str(msg.location)}))

    def _on_page_error(self, error: Any) -> None:
        text = str(error).splitlines()[0][:300] if str(error) else "Uncaught exception"
        if self.recorder:
            self.recorder.log("exception", text)
        self._push(Signal(kind="page_error", message=text, url=self.page.url, detail={"stack": str(getattr(error, "stack", ""))[:600]}))

    def _on_dialog(self, dialog: Any) -> None:
        self._dialogs += 1
        self._spawn(dialog.accept())

    def _relative(self, url: str) -> str:
        return url[len(self.origin) :] or "/" if url.startswith(self.origin) else url

    def forget_signals(self) -> None:
        """Start with a clean slate (after sign-in, before the work being judged)."""
        self._pending.clear()
        self._seen.clear()

    def drain(self, step: int) -> list[Signal]:
        out, self._pending = self._pending, []
        for s in out:
            s.step = step
        return out

    def note(self, signal: Signal) -> None:
        self._push(signal)

    # ------------------------------------------------------------------ waiting

    def _busy(self) -> bool:
        now = time.monotonic()
        return any(now - started < _LONG_REQUEST_S for started in self._inflight.values())

    async def settle(self) -> None:
        """Wait until the page has finished reacting - by asking it, not by sleeping.

        Two independent views must agree before a step is considered over:

        * the page's own (`PAGE_QUIET_JS`): no fetch/XHR in flight, no navigation
          pending, images loaded, and no DOM mutation for `settle_quiet_ms`. It is
          synchronous with the app's code, so it cannot be fooled by late events;
        * the browser's (CDP request events): no request in flight, which also
          covers response bodies and sub-resources the page-side view cannot see.

        An action that triggers nothing costs one short quiet window. A slow
        page is bounded by `settle_max_ms`. Long-lived requests (polling,
        streams) stop counting after two seconds.
        """
        deadline = time.monotonic() + self.s.settle_max_ms / 1000
        while (left := deadline - time.monotonic()) > 0:
            seen = self._requests
            with suppress(PlaywrightError):
                await self.page.wait_for_load_state("domcontentloaded", timeout=left * 1000)
            while self._busy() and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            try:
                await self.page.evaluate(PAGE_QUIET_JS, [self.s.settle_quiet_ms, _DOM_WAIT_MAX_MS, max(1.0, (deadline - time.monotonic()) * 1000)])
            except PlaywrightError:
                continue  # the document was replaced under us: a navigation committed
            if self._requests == seen and not self._busy():
                return

    # ------------------------------------------------------------------ observe / act

    async def goto(self, path: str) -> None:
        self._own_navigation = True
        try:
            await self.page.goto(urljoin(self.base_url + "/", path.lstrip("/")), wait_until="domcontentloaded", timeout=self.s.nav_timeout_ms)
        finally:
            self._own_navigation = False
        await self.settle()

    async def observe(self) -> Observation:
        raw: dict[str, Any] | None = None
        for _ in range(4):
            try:
                raw = await self.page.evaluate(SNAPSHOT_JS, "e")
                break
            except PlaywrightError:  # execution context destroyed by a navigation in progress
                await self.settle()
        if raw is None:
            raw = await self.page.evaluate(SNAPSHOT_JS, "e")
        obs = Observation.from_raw(merge(raw, await self._frames()))
        self.refs_valid = True
        for issue in obs.a11y:
            self._push(Signal(kind="a11y", message=f"{issue['rule']}: {issue.get('html', '')}", url=obs.url, detail=issue))
        for src in obs.broken_images:
            self._push(Signal(kind="broken_image", message=f"Image failed to load: {url_path(src)}", url=obs.url, detail={"src": src}))
        if obs.overflow_x > 8:
            detail = {"rule": "horizontal-overflow", "px": obs.overflow_x, "device": self.device, "viewport": list(obs.viewport)}
            self._push(Signal(kind="layout", message=f"Page overflows the viewport horizontally by {obs.overflow_x}px", url=obs.url, detail=detail))
        return obs

    async def _frames(self) -> list[tuple[str, dict[str, Any]]]:
        """Snapshots of the app's own iframes that are on show. A page without frames pays nothing."""
        if len(self.page.frames) < 2:
            return []
        out: list[tuple[str, dict[str, Any]]] = []
        for i, (key, frame) in enumerate(app_frames(self.page, self.origin), 1):
            with suppress(PlaywrightError):  # a frame can go away between being listed and being read
                if await (await frame.frame_element()).is_visible():
                    out.append((key, await frame.evaluate(SNAPSHOT_JS, f"f{i}e")))
        return out

    async def reset(self, path: str) -> str:
        """Ask the app to put its test data back (its own endpoint). Empty when it did, else why not.

        The request carries this session's cookies, so an endpoint that resets "my account"
        resets the account the session is signed in as.
        """
        try:
            response = await self.context.request.post(urljoin(self.base_url + "/", path.lstrip("/")), timeout=self.s.nav_timeout_ms)
        except PlaywrightError as exc:
            return (str(exc).splitlines() or ["the request failed"])[0][:200]
        return "" if response.ok else f"it answered {response.status}"

    async def act(self, action: Action, *, live: bool = False) -> ActResult:
        """Execute one action. `live` means its ref comes from the current observation."""
        t0 = time.monotonic()
        url_before, req_before, dlg_before = self.page.url, self._requests, self._dialogs
        res = ActResult()
        timeout = self.s.action_timeout_ms
        try:
            if action.kind == "navigate":
                await self.goto(action.value)
            elif action.kind == "back":
                await self.page.go_back(wait_until="domcontentloaded", timeout=self.s.nav_timeout_ms)
            elif action.kind == "press":
                await self.page.keyboard.press(action.value or "Enter")
            elif action.kind == "wait":
                await asyncio.sleep(min(int(action.value or 300), 3000) / 1000)
            elif action.target is None:
                raise ElementNotFound(f"{action.kind} needs a target element")
            else:
                loc, res.healed = await resolve(self.page, action.target, live=live and self.refs_valid, origin=self.origin)
                if res.healed:
                    self.refs_valid = False  # healing re-tagged the DOM; older refs are void
                if action.kind == "upload":
                    await loc.set_input_files(samples.payload(action.value), timeout=timeout)  # type: ignore[arg-type]
                elif action.kind == "drag":
                    if action.to is None:
                        raise ElementNotFound("a drag needs somewhere to drop")
                    onto, healed = await resolve(self.page, action.to, live=live and self.refs_valid, origin=self.origin)
                    if healed:
                        res.healed, self.refs_valid = True, False
                    await loc.drag_to(onto, timeout=timeout)
                elif action.kind == "click":
                    await loc.click(timeout=timeout)
                elif action.kind == "fill":
                    await loc.fill(self._secret(action.secret) if action.secret else action.value, timeout=timeout)
                elif action.kind == "select":
                    await loc.select_option(action.value, timeout=timeout)
                elif action.kind == "check":
                    await loc.check(timeout=timeout)
                elif action.kind == "hover":
                    await loc.hover(timeout=timeout)
        except (PlaywrightError, ElementNotFound, ValueError) as exc:
            res.ok = False
            res.error = (str(exc).splitlines() or ["action failed"])[0][:240]
        if action.kind != "navigate":
            await self.settle()
        res.navigated = self.page.url != url_before
        res.requests = self._requests - req_before
        res.dialogs = self._dialogs - dlg_before
        res.duration_ms = int((time.monotonic() - t0) * 1000)
        return res

    def _secret(self, field: str) -> str:
        if self.credential is None or field not in ("username", "password"):
            raise ValueError(f"no credential available for '{field}'")
        return getattr(self.credential, field)

    async def screenshot(self) -> str:
        if self.artifacts is None:
            return ""
        with suppress(PlaywrightError):
            return await self.artifacts.put(await self.page.screenshot(type="jpeg", quality=55), "jpg")
        return ""

    async def login(self, recipe: LoginRecipe, credential: Credential) -> bool:
        """Deterministic sign-in; the model never sees or types the password."""
        page = self.page
        await self.goto(recipe.path)
        password = page.locator(recipe.password_selector or 'input[type="password"]').first
        if await password.count() == 0:
            return False
        default_user = 'form:has(input[type="password"]) input:not([type="password"]):not([type="hidden"]):not([type="submit"]):not([type="checkbox"])'
        user = page.locator(recipe.username_selector or default_user).first
        try:
            await user.fill(credential.username, timeout=self.s.action_timeout_ms)
            await password.fill(credential.password, timeout=self.s.action_timeout_ms)
            if recipe.submit_selector:
                await page.locator(recipe.submit_selector).first.click(timeout=self.s.action_timeout_ms)
            else:
                await password.press("Enter")
        except PlaywrightError:
            return False
        await self.settle()
        still_on_form = url_path(page.url) == recipe.path and await page.locator('input[type="password"]').count() > 0
        return not still_on_form

    # ------------------------------------------------------------------ typed checks

    async def run_check(self, check: Check, obs: Observation, signals: list[Signal]) -> CheckResult:
        if not check.well_formed():
            return CheckResult(check=check, passed=False, detail="the check is empty", evaluable=False)
        ok, detail, evaluable = False, "", True
        url = self.page.url
        match check.kind:
            case "url_contains":
                ok, detail = check.value in url, url
            case "url_matches":
                ok, detail = re.search(check.value, url) is not None, url
            case "title_contains":
                ok, detail = norm_text(check.value) in norm_text(obs.title), obs.title
            case "text_visible" | "text_absent":
                visible = False
                with suppress(PlaywrightError):
                    visible = await self.page.get_by_text(check.value, exact=False).first.is_visible()
                ok = visible if check.kind == "text_visible" else not visible
                detail = "visible" if visible else "not visible"
            case "element_visible" | "element_absent":
                found = obs.has(check.role, check.name) is not None
                ok = found if check.kind == "element_visible" else not found
                detail = "present" if found else "absent"
            case "value_equals":
                el = obs.has(check.role, check.name)
                ok, detail, evaluable = (el is not None and el.value == check.value), (el.value if el else "element absent"), el is not None
            case "no_signal":
                hit = next((s for s in signals if s.signature == check.value), None)
                ok, detail = hit is None, (hit.message if hit else "")
            case "no_http_error":
                hit = next((s for s in signals if s.kind == "http_error" and check.value in str(s.detail.get("url", ""))), None)
                ok, detail = hit is None, (hit.message if hit else "")
        return CheckResult(check=check, passed=ok, detail=str(detail)[:200], evaluable=evaluable)


def dead_click_signal(action: Action, res: ActResult, before: Observation, after: Observation) -> Signal | None:
    """A control that is clicked and changes *nothing*: byte-identical DOM, no
    navigation, no dialog, no request started by the page, and no DOM-invisible
    side effect (clipboard, storage, history, print...). HTML5 validation
    blocking a submit is excluded.

    "No request" is read from the page's own call counter, not from browser
    network events: an unrelated request that happens to land in the same
    window (a favicon, a late image) must not change the verdict, or the same
    click would be judged differently from one replay to the next.
    """
    el = action.target
    if action.kind != "click" or el is None or not res.ok or res.navigated or res.dialogs:
        return None
    if before.calls != after.calls or (after.calls < 0 and res.requests):
        return None  # the page started a request (or cannot tell us, and the browser saw one)
    if el.role not in ("button", "clickable") or before.dom != after.dom or not before.dom:
        return None
    if before.fx != after.fx or before.url != after.url:
        return None
    now = next((e for e in after.elements if e.key() == el.key()), None)
    if now is not None and now.attrs.get("form_invalid"):
        return None  # the browser blocked the submit: that is validation working
    return Signal(
        kind="dead_click",
        message=f"Clicking {el.label()} has no observable effect",
        url=after.url,
        detail={"element": el.key(), "label": el.label(), "css": el.css},
    )
