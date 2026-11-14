"""What a stranger can make a server do: guess passwords cheaply, or send its browsers somewhere they must not go.

A team server is reachable by people who have no account. Checking a password is the one expensive
thing they can ask for, so it must not stall the server, and guessing must be slowed down whether it
aims at one account or at many. And a scan opens whatever address it is given, so the one kind of
address that gives a cloud machine's credentials away is refused.
"""

from __future__ import annotations

import asyncio
import hashlib
import http.server
import threading
from collections.abc import Iterator

import httpx
import pytest
from playwright.async_api import Error as PlaywrightError
from test_team import OWNER, set_up

from catchbunny import accounts as accounts_module
from catchbunny.accounts import STRETCHING_AT_ONCE, AccountError, Accounts
from catchbunny.api import Serving, create_app
from catchbunny.browser.netguard import refusal
from catchbunny.engine.orchestrator import App


@pytest.fixture
def stretched(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """One entry per scrypt run: whether it ran on the event loop's own thread."""
    loop_thread = threading.get_ident()
    calls: list[bool] = []
    real = hashlib.scrypt

    def scrypt(*args, **kw):  # type: ignore[no-untyped-def]
        calls.append(threading.get_ident() == loop_thread)
        return real(*args, **kw)

    monkeypatch.setattr(accounts_module.hashlib, "scrypt", scrypt)
    return calls


async def test_checking_a_password_never_stalls_the_server(app: App, stretched: list[bool]) -> None:
    team = Accounts(app.store, app.settings.data_dir)
    await team.create_owner(team.setup_code(), **OWNER)
    for email in ("nobody@example.com", OWNER["email"]):
        with pytest.raises(AccountError):
            await team.sign_in(email, "a wrong password")
    assert await team.sign_in(OWNER["email"], OWNER["password"])
    assert len(stretched) == 4, "one stretch per check: an account that does not exist costs what one that does costs"
    assert not any(stretched), "scrypt runs in a thread, never on the loop that serves every other request"


async def test_a_burst_of_guesses_is_refused_without_being_stretched(app: App, stretched: list[bool]) -> None:
    team = Accounts(app.store, app.settings.data_dir)
    await team.create_owner(team.setup_code(), **OWNER)
    stretched.clear()

    async def guess(i: int) -> int:
        try:
            await team.sign_in(OWNER["email"], f"guess number {i}")
        except AccountError as e:
            return e.status
        return 200

    answers = await asyncio.gather(*(guess(i) for i in range(40)))
    most = Accounts.PER_EMAIL + STRETCHING_AT_ONCE - 1  # guesses already admitted when the limit is reached still finish
    assert answers.count(401) <= most and answers.count(429) >= 40 - most and set(answers) == {401, 429}
    assert len(stretched) <= most, "a refused guess is refused before it costs a stretch"


async def test_guessing_from_one_address_is_limited_whichever_accounts_it_tries(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Accounts, "PER_CLIENT", 3)
    api = create_app(app, serving=Serving(team=True, public_url="http://team.test"))

    def client(address: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=api, client=(address, 4000)), base_url="http://team.test")

    async with client("198.51.100.1") as owner:
        await set_up(app, owner)
    async with client("203.0.113.7") as bot:
        answers = [(await bot.post("/v1/auth/sign-in", json={"email": f"person{i}@example.com", "password": "Summer2026!"})).status_code for i in range(5)]
        assert answers == [401, 401, 401, 429, 429], "a new email each time does not reset the count for the address"
        assert (await bot.post("/v1/auth/join", json={"code": "AAAA-BBBB-CCCC", "name": "Bot", "email": "bot@example.com", "password": "long enough password"})).status_code == 429
    async with client("198.51.100.1") as owner:
        assert (await owner.post("/v1/auth/sign-in", json={"email": OWNER["email"], "password": OWNER["password"]})).status_code == 200, "someone else is not slowed down"


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://2852039166/",  # 169.254.169.254 written as one number
        "http://0xa9fea9fe/",
        "http://[::ffff:169.254.169.254]/",
        "http://[fd00:ec2::254]/",
        "http://100.100.100.200/",
        "http://[fe80::1%25en0]/",
    ],
)
async def test_a_metadata_address_is_refused_however_it_is_written(url: str) -> None:
    assert await refusal(url)


@pytest.mark.parametrize("url", ["http://localhost:3000/", "http://127.0.0.1:8811/", "http://10.0.0.5/", "http://192.168.1.20:8080/", "http://[::1]:3000/", "about:blank"])
async def test_the_machine_itself_and_private_networks_stay_open(url: str) -> None:
    assert await refusal(url) is None, "a dev server on localhost and staging on the office network are what Catchbunny is for"


async def test_a_run_at_a_metadata_address_is_blocked_before_a_browser_opens(app: App) -> None:
    api = create_app(app, serving=Serving(local_only=True))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://127.0.0.1:8700") as http:
        project = (await http.post("/v1/projects", json={"name": "not an app", "base_url": "http://169.254.169.254/"})).json()
        started = await http.post(f"/v1/projects/{project['id']}/runs", json={"kind": "deep_scan", "brain": "heuristic", "testers": 1, "max_steps": 10})
        run = await app.runs.wait(started.json()["id"], timeout=30)
    assert run.status.value == "blocked" and "metadata service" in (run.error or ""), run.error
    assert app.pool.stats()["contexts_opened"] == 0


@pytest.fixture
def hostile() -> Iterator[str]:
    """An app whose page points the browser at the metadata service, as a hostile page could."""

    class Pages(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b"<a href='http://169.254.169.254/'>credentials</a><img src='http://metadata.google.internal/x.png'>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


async def test_on_a_team_server_no_link_or_page_reaches_the_metadata_service(app: App, hostile: str) -> None:
    app.settings.guard_network = True  # what `catchbunny start --team` sets
    async with app.pool.context() as ctx:
        page = await ctx.new_page()
        refused: list[str] = []
        page.on("requestfailed", lambda r: refused.append(f"{r.url} {r.failure}"))
        opened = await page.goto(hostile + "/")
        assert opened and opened.ok, "the app itself opens as usual"
        await page.wait_for_load_state("load")
        async with page.expect_event("requestfailed", lambda r: "169.254.169.254" in r.url, timeout=5000):
            await page.click("a")
        assert "169.254" not in page.url, "the link leads to the browser's own error page, not to the service"
        with pytest.raises(PlaywrightError, match="ERR_BLOCKED_BY_CLIENT"):
            await page.goto("http://2852039166/latest/meta-data/", timeout=5000)
        assert any("metadata.google.internal" in r for r in refused) and any("169.254.169.254" in r for r in refused), refused
