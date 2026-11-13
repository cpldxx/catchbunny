"""The site and the console in a real browser: which way in is offered where, and
that the ways in work. Needs the built site (web/dist); skipped without it."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from conftest import free_port, serving
from playwright.async_api import Page, expect
from starlette.applications import Starlette
from starlette.responses import FileResponse
from starlette.routing import Route

from catchbunny.accounts import Accounts
from catchbunny.api import WEB_DIST, Serving, serve_app
from catchbunny.engine.orchestrator import App

pytestmark = pytest.mark.skipif(not (WEB_DIST / "index.html").is_file(), reason="the site is not built: (cd web && npm install && npm run build)")


@asynccontextmanager
async def browser(app: App) -> AsyncIterator[Page]:
    async with app.pool.context() as ctx:
        page = await ctx.new_page()
        page.set_default_timeout(10_000)
        yield page


def static_copy() -> Starlette:
    """The site with no Catchbunny behind it, as a static host serves it: any unknown path is the page."""

    async def serve(request):  # type: ignore[no-untyped-def]
        file = (WEB_DIST / request.path_params["path"]).resolve()
        return FileResponse(file if file.is_relative_to(WEB_DIST) and file.is_file() else WEB_DIST / "index.html")

    return Starlette(routes=[Route("/{path:path}", serve)])


async def test_a_static_copy_of_the_site_does_not_offer_a_console_it_does_not_have(app: App) -> None:
    async with serving(static_copy()) as base, browser(app) as page:
        await page.goto(base)
        await expect(page.get_by_role("heading", level=1)).to_contain_text("prove a bug")
        await expect(page.locator(".hero").get_by_role("link", name="Connect your agent")).to_be_visible()
        await expect(page.get_by_role("link", name="Open the console")).to_have_count(0)
        await expect(page.locator(".nav-cta")).to_have_text("Get started")
        await page.goto(f"{base}/app")
        await expect(page.get_by_role("heading", level=1)).to_have_text("The console runs where Catchbunny runs")
        await expect(page.get_by_text("raw.githubusercontent.com/cpldxx/catchbunny/main/install.sh")).to_be_visible(), "what a stranger can paste: no checkout, no Node"
        await expect(page.get_by_text("catchbunny start", exact=True)).to_be_visible()


async def test_on_your_own_machine_the_console_opens_with_no_sign_in(app: App) -> None:
    async with serving(serve_app(app, Serving(local_only=True))) as base, browser(app) as page:
        await page.goto(base)
        door = page.locator(".hero").get_by_role("link", name="Open the console")
        await expect(door).to_be_visible()
        await door.click()
        await expect(page.get_by_role("heading", level=1)).to_have_text("Apps under test")
        await expect(page.get_by_role("link", name="Team")).to_have_count(0), "no accounts, so no team page"
        await page.get_by_role("link", name="Connect an agent").click()
        await expect(page.locator(".endpoint code")).to_have_text(f"{base}/mcp")
        await page.get_by_role("button", name="Claude Code").click()
        await expect(page.locator(".lines pre")).to_have_text(f"claude mcp add --transport http catchbunny {base}/mcp")
        await page.get_by_role("button", name="ChatGPT", exact=True).click()
        await expect(page.get_by_text("cannot reach")).to_be_visible(), "a connector on the internet cannot reach localhost, and the page says so"
        await page.goto(f"{base}/app/sign-in")
        await expect(page.get_by_role("heading", level=1)).to_have_text("Apps under test"), "there is nothing to sign in to"


async def test_a_team_server_is_set_up_joined_and_signed_in_to_through_its_pages(app: App) -> None:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    async with serving(serve_app(app, Serving(team=True, public_url=base)), port), browser(app) as owner:
        code = Accounts(app.store, app.settings.data_dir).setup_code()
        await owner.goto(base)
        await expect(owner.locator(".hero").get_by_role("link", name="Set up this server")).to_be_visible()
        await owner.goto(f"{base}/app")
        await expect(owner).to_have_url(f"{base}/app/setup"), "nothing inside is shown before there is an owner"
        await owner.goto(f"{base}/app/setup?code={code}")
        await expect(owner.get_by_label("Setup code")).to_have_value(code)
        await owner.get_by_label("Your name").fill("Ada Owner")
        await owner.get_by_label("Email").fill("ada@example.com")
        await owner.get_by_label("Password").fill("short")
        await owner.get_by_role("button", name="Create the owner account").click()
        await expect(owner.get_by_text("at least 10 characters").last).to_be_visible()
        await owner.get_by_label("Password").fill("correct horse battery")
        await owner.get_by_role("button", name="Create the owner account").click()
        await expect(owner.get_by_role("heading", level=1)).to_have_text("Team")
        team_code = (await owner.locator(".invite-code").inner_text()).strip()
        assert len(team_code) == 14 and team_code != code

        async with browser(app) as member:  # a colleague, in a browser of their own
            await member.goto(f"{base}/app/p/anything/runs")
            await expect(member).to_have_url(f"{base}/app/sign-in?next=%2Fapp%2Fp%2Fanything%2Fruns")
            await member.get_by_label("Email").fill("ben@example.com")
            await member.get_by_label("Password").fill("not my password")
            await member.get_by_role("button", name="Sign in").click()
            await expect(member.get_by_text("The email or the password is wrong.")).to_be_visible()
            await member.get_by_role("link", name="Join with a team code").click()
            await member.get_by_label("Team code").fill(team_code)
            await member.get_by_label("Your name").fill("Ben Member")
            await member.get_by_label("Email").fill("ben@example.com")
            await member.get_by_label("Password").fill("another long password")
            await member.get_by_role("button", name="Create my account").click()
            await expect(member.get_by_role("link", name="Team")).to_be_visible()
            await member.get_by_role("link", name="Team").click()
            await expect(member.locator(".people li")).to_have_count(2)
            await expect(member.locator(".invite-code")).to_have_count(0), "only the owner sees the code"

            # A key for an agent: made on the Connect page, shown once.
            await member.get_by_role("link", name="Connect an agent").click()
            await member.get_by_label("What the key is for").fill("CI")
            await member.get_by_role("button", name="Create a key").click()
            secret = await member.locator(".ant-modal .cmd code").inner_text()
            assert secret.startswith("cb_")
            await member.get_by_role("button", name="I have copied it").click()
            await expect(member.locator(".key-list li")).to_have_count(1)
            await expect(member.locator(".key-list")).not_to_contain_text(secret)

            await member.get_by_role("button", name="Sign out").click()
            await expect(member.get_by_role("heading", level=1)).to_have_text("Sign in")
            await member.goto(base)
            await expect(member.locator(".hero").get_by_role("link", name="Sign in")).to_be_visible()

        await owner.reload()
        await expect(owner.locator(".people li")).to_have_count(2)
