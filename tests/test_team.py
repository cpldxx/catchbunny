"""A server shared by a team: who may ask it anything, how people get in, and how
an agent or a connector reaches the MCP endpoint on the same address."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import httpx2
import pytest
from conftest import free_port, serving
from mcp import Client
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from catchbunny.accounts import Accounts, check_password, hash_password
from catchbunny.api import COOKIE, Serving, create_app, serve_app
from catchbunny.engine.orchestrator import App

OWNER = {"name": "Ada Owner", "email": "ada@example.com", "password": "correct horse battery"}
MEMBER = {"name": "Ben Member", "email": "ben@example.com", "password": "another long password"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}
ACCEPT = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


def browser(api: Any) -> httpx.AsyncClient:
    """A client that keeps cookies, as a signed-in browser does."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://team.test")


async def set_up(app: App, http: httpx.AsyncClient) -> str:
    """Create the owner the way a person does: with the code from the terminal. Returns the team code."""
    code = Accounts(app.store, app.settings.data_dir).setup_code()
    created = await http.post("/v1/auth/setup", json={"code": code, **OWNER})
    assert created.status_code == 201, created.text
    return (await http.get("/v1/team")).json()["code"]


def test_a_password_is_stretched_and_never_kept() -> None:
    stored = hash_password("correct horse battery")
    assert stored.startswith("scrypt$") and "correct" not in stored
    assert check_password("correct horse battery", stored) and not check_password("correct horse batterz", stored) and not check_password("x", "nonsense")


async def test_a_team_server_lets_nobody_in_until_they_sign_in(app: App) -> None:
    api = create_app(app, serving=Serving(team=True, public_url="http://team.test"))
    async with browser(api) as http:
        meta = (await http.get("/v1/meta")).json()
        assert meta["mode"] == "team" and meta["needs_owner"] and meta["user"] is None and meta["mcp_url"] == "http://team.test/mcp"
        refused = await http.get("/v1/projects")
        assert refused.status_code == 401 and refused.json()["code"] == "UNAUTHENTICATED"
        assert (await http.get("/healthz")).status_code == 200, "whether it is up is not a secret"

        wrong = await http.post("/v1/auth/setup", json={"code": "AAAA-BBBB-CCCC", **OWNER})
        assert wrong.status_code == 403 and "terminal" in wrong.json()["message"]
        team_code = await set_up(app, http)
        assert http.cookies.get(COOKIE) and (await http.get("/v1/projects")).json() == []
        again = await http.post("/v1/auth/setup", json={"code": "anything", **OWNER})
        assert again.status_code == 409, "there is one owner, and the setup code is spent"
        assert not (app.settings.data_dir / "setup_code").exists()
        meta = (await http.get("/v1/meta")).json()
        assert meta["user"]["role"] == "owner" and "password" not in meta["user"] and not meta["needs_owner"]

        async with browser(api) as colleague:
            assert (await colleague.post("/v1/auth/join", json={"code": "WRONG-CODE-HERE", **MEMBER})).status_code == 403
            assert (await colleague.post("/v1/auth/join", json={"code": team_code, **MEMBER, "password": "short"})).status_code == 422
            joined = await colleague.post("/v1/auth/join", json={"code": team_code.lower(), **MEMBER})
            assert joined.status_code == 201 and joined.json()["role"] == "member", "a code typed in lower case is the same code"
            assert (await colleague.post("/v1/auth/join", json={"code": team_code, **MEMBER})).status_code == 409
            seen = (await colleague.get("/v1/team")).json()
            assert seen["code"] == "" and [m["email"] for m in seen["members"]] == [OWNER["email"], MEMBER["email"]], "only the owner sees the code"
            assert (await colleague.post("/v1/team/code")).status_code == 403

            await colleague.post("/v1/auth/sign-out")
            assert (await colleague.get("/v1/projects")).status_code == 401
            assert (await colleague.post("/v1/auth/sign-in", json={"email": MEMBER["email"], "password": "not it at all"})).status_code == 401
            back = await colleague.post("/v1/auth/sign-in", json={"email": " BEN@example.com ", "password": MEMBER["password"]})
            assert back.status_code == 200 and (await colleague.get("/v1/projects")).status_code == 200

            # A key for an agent: shown once, used as a header, gone when revoked.
            made = (await colleague.post("/v1/tokens", json={"label": "Claude Code on my laptop"})).json()
            secret = made["secret"]
            assert secret.startswith("cb_") and made["hint"] == secret[-4:]
            listed = (await colleague.get("/v1/tokens")).json()
            assert [t["label"] for t in listed] == ["Claude Code on my laptop"] and "secret" not in listed[0] and secret not in str(listed)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://team.test", headers={"authorization": f"Bearer {secret}"}) as agent:
                assert (await agent.get("/v1/projects")).status_code == 200
                assert (await agent.get("/v1/meta")).json()["user"]["email"] == MEMBER["email"]

                # The owner replaces the code and removes the member: the old code, their browser and their key all stop working.
                new_code = (await http.post("/v1/team/code")).json()["code"]
                assert new_code != team_code
                member_id = next(m["id"] for m in (await http.get("/v1/team")).json()["members"] if not m["you"])
                assert (await http.delete(f"/v1/team/members/{(await http.get('/v1/meta')).json()['user']['id']}")).status_code == 409
                assert (await http.delete(f"/v1/team/members/{member_id}")).status_code == 204
                assert (await agent.get("/v1/projects")).status_code == 401 and (await colleague.get("/v1/projects")).status_code == 401
            async with browser(api) as late:
                assert (await late.post("/v1/auth/join", json={"code": team_code, **MEMBER})).status_code == 403
                assert (await late.post("/v1/auth/join", json={"code": new_code, **MEMBER})).status_code == 201


async def test_a_cookie_is_not_consent_to_what_another_site_asks_for(app: App) -> None:
    api = create_app(app, serving=Serving(team=True, public_url="http://team.test"))
    async with browser(api) as http:
        await set_up(app, http)
        project = {"name": "shop", "base_url": "http://127.0.0.1:9"}
        assert (await http.post("/v1/projects", json=project, headers={"origin": "https://evil.example"})).status_code == 403
        assert (await http.post("/v1/projects", json=project, headers={"origin": "http://team.test"})).status_code == 201
        assert (await http.post("/v1/projects", json=project)).status_code == 201, "a request that is not from a page carries no origin"


async def test_guessing_is_slowed_down(app: App) -> None:
    api = create_app(app, serving=Serving(team=True, public_url="http://team.test"))
    async with browser(api) as http:
        await set_up(app, http)
        answers = [(await http.post("/v1/auth/sign-in", json={"email": OWNER["email"], "password": f"guess number {i}"})).status_code for i in range(10)]
        assert answers[:8] == [401] * 8 and answers[8:] == [429, 429]
        assert (await http.post("/v1/auth/sign-in", json=OWNER)).status_code == 429, "even the right password waits"


async def test_on_your_own_machine_there_are_no_accounts_and_only_localhost_is_answered(app: App) -> None:
    api = create_app(app, serving=Serving(local_only=True))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://127.0.0.1:8700") as http:
        meta = (await http.get("/v1/meta")).json()
        assert meta["mode"] == "local" and meta["user"] is None and not meta["needs_owner"] and set(meta["browsers"]) == {"chromium", "firefox", "webkit"}
        assert (await http.get("/v1/projects")).status_code == 200
        assert (await http.get("/v1/team")).status_code == 404 and (await http.post("/v1/auth/sign-in", json={"email": "a@b.co", "password": "x"})).status_code == 404
        assert (await http.get("/v1/projects", headers={"host": "localhost:8700"})).status_code == 200
        assert (await http.get("/v1/projects", headers={"host": "[::1]:8700"})).status_code == 200
        rebound = await http.get("/v1/projects", headers={"host": "attacker.example:8700"})
        assert rebound.status_code == 403 and "--team" in rebound.json()["message"], "a page that points its own name at 127.0.0.1 is not you"
        preflight = await http.options("/v1/projects", headers={"origin": "https://evil.example", "access-control-request-method": "POST"})
        assert "access-control-allow-origin" not in preflight.headers, "no other site may read a local server"


# ---------------------------------------------------------------- the MCP endpoint on the same address


async def test_serve_is_the_whole_product_on_one_port(app: App) -> None:
    """Locally: the API and the MCP endpoint side by side, both answering only to this machine."""
    async with serving(serve_app(app, Serving(local_only=True))) as base, httpx2.AsyncClient() as http:
        assert (await http.get(f"{base}/v1/meta")).json()["mcp_url"] == f"{base}/mcp"
        async with Client(streamable_http_client(f"{base}/mcp"), raise_exceptions=True) as agent:
            assert {t.name for t in (await agent.list_tools()).tools} >= {"scan", "run_status", "get_finding", "verify_fix"}
        assert (await http.post(f"{base}/mcp", json=INIT, headers={**ACCEPT, "host": "attacker.example"})).status_code in (400, 403, 421)


async def test_an_agent_reaches_a_team_server_with_a_key(app: App) -> None:
    async with serving(serve_app(app, Serving(team=True, public_url="http://10.0.0.5:8700"))) as base:  # no HTTPS: keys only
        async with httpx.AsyncClient(base_url=base) as http:
            await set_up(app, http)
            secret = (await http.post("/v1/tokens", json={"label": "CI"})).json()["secret"]
            assert (await http.get("/.well-known/oauth-authorization-server")).status_code != 200, "no sign-in dance is offered without HTTPS"
        async with httpx2.AsyncClient() as anonymous:
            assert (await anonymous.post(f"{base}/mcp", json=INIT, headers=ACCEPT)).status_code == 401
            assert (await anonymous.post(f"{base}/mcp", json=INIT, headers={**ACCEPT, "authorization": "Bearer cb_made-up"})).status_code == 401
        async with httpx2.AsyncClient(headers={"authorization": f"Bearer {secret}"}) as authed:
            async with Client(streamable_http_client(f"{base}/mcp", http_client=authed), raise_exceptions=True) as agent:
                assert len((await agent.list_tools()).tools) == 7
                missing = await agent.call_tool("run_status", {"run_id": "run_nope"})
                assert missing.is_error and "not found" in str(missing.content), "the tools answer, as that person"


class Memory:
    """Where an OAuth client keeps what it was given."""

    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client = client_info


@pytest.mark.parametrize("approve", [True, False])
async def test_a_connector_signs_a_person_in_and_then_calls_the_tools(app: App, approve: bool) -> None:
    """The whole dance, driven by the MCP SDK's own OAuth client: find the server's metadata,
    register, send the person to sign in and agree, trade the code, call a tool."""
    port = free_port()  # a server that signs connectors in has to know its own address before it starts
    base = f"http://127.0.0.1:{port}"
    async with serving(serve_app(app, Serving(team=True, public_url=base)), port):
        person = httpx.AsyncClient(base_url=base)  # the person's browser, signed in to the console
        await set_up(app, person)
        seen: dict[str, Any] = {}

        async def send_person_to(url: str) -> None:
            """What a connector does with the authorization URL: open it in the person's browser."""
            landed = await person.get(url)  # /authorize checks the request and sends the browser to the consent page
            assert landed.status_code == 302 and landed.headers["location"].startswith(f"{base}/app/authorize?request="), landed.text
            request_id = parse_qs(urlsplit(landed.headers["location"]).query)["request"][0]
            shown = (await person.get(f"/v1/oauth/requests/{request_id}")).json()
            assert shown == {"client_name": "Test Connector", "client_uri": "", "redirect_host": "127.0.0.1"}
            answered = (await person.post(f"/v1/oauth/requests/{request_id}", json={"approve": approve})).json()
            seen.update({k: v[0] for k, v in parse_qs(urlsplit(answered["redirect_to"]).query).items()})
            assert (await person.post(f"/v1/oauth/requests/{request_id}", json={"approve": True})).status_code == 404, "a request is answered once"

        async def come_back() -> AuthorizationCodeResult:
            return AuthorizationCodeResult(code=seen.get("code", ""), state=seen.get("state"))

        memory = Memory()
        metadata = OAuthClientMetadata(client_name="Test Connector", redirect_uris=["http://127.0.0.1:9999/callback"], grant_types=["authorization_code", "refresh_token"], response_types=["code"], token_endpoint_auth_method="none")  # type: ignore[list-item]
        auth = OAuthClientProvider(server_url=f"{base}/mcp", client_metadata=metadata, storage=memory, redirect_handler=send_person_to, callback_handler=come_back)
        async with httpx2.AsyncClient(auth=auth) as http:
            if not approve:
                with pytest.raises(Exception):  # noqa: B017, PT011 - the client gives up; how it words that is its business
                    async with Client(streamable_http_client(f"{base}/mcp", http_client=http), raise_exceptions=True) as agent:
                        await agent.list_tools()
                assert seen["error"] == "access_denied" and memory.tokens is None
                return
            async with Client(streamable_http_client(f"{base}/mcp", http_client=http), raise_exceptions=True) as agent:
                assert len((await agent.list_tools()).tools) == 7
        assert memory.tokens is not None and memory.tokens.refresh_token and memory.client is not None and seen["state"]
        access = memory.tokens.access_token

        async with httpx2.AsyncClient() as raw:
            # The token is that person: it opens the HTTP API as well, and nothing else does.
            assert (await raw.get(f"{base}/v1/meta", headers={"authorization": f"Bearer {access}"})).json()["user"]["email"] == OWNER["email"]
            assert (await raw.post(f"{base}/mcp", json=INIT, headers={**ACCEPT, "authorization": f"Bearer {access}x"})).status_code == 401
            challenge = (await raw.post(f"{base}/mcp", json=INIT, headers=ACCEPT)).headers["www-authenticate"]
            assert f"{base}/.well-known/oauth-protected-resource/mcp" in challenge, "an unauthenticated client is told where to start"

            # A refresh token is used once: the pair it replaces stops working.
            form = {"grant_type": "refresh_token", "refresh_token": memory.tokens.refresh_token, "client_id": memory.client.client_id}
            fresh = await raw.post(f"{base}/token", data=form)
            assert fresh.status_code == 200 and fresh.json()["access_token"] != access
            assert (await raw.post(f"{base}/token", data=form)).status_code == 400
            assert (await raw.post(f"{base}/mcp", json=INIT, headers={**ACCEPT, "authorization": f"Bearer {access}"})).status_code == 401
            assert (await raw.post(f"{base}/mcp", json=INIT, headers={**ACCEPT, "authorization": f"Bearer {fresh.json()['access_token']}"})).status_code == 200
        await person.aclose()
