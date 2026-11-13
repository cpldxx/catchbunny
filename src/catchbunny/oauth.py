"""Signing a connector in: OAuth for the MCP endpoint of a team server.

Claude and ChatGPT add a remote MCP server by sending the person to it to sign
in. The MCP SDK implements the protocol side of that (metadata, dynamic client
registration, PKCE, the token endpoint); what it asks of us is this provider:
remember the clients, ask a signed-in person whether to let one in, and mint
and check the tokens.

    connector                       this server                     the person
    ---------                       -----------                     ----------
    POST /register        ->        a client id
    GET  /authorize       ->        302 to /app/authorize?request=..
                                    (the console's consent page)  -> signs in, says yes
                          <-        302 back with a code
    POST /token (code + PKCE)  ->   an access token and a refresh token
    POST /mcp  (Bearer token)  ->   the tools, as that person

Tokens are random strings kept only as hashes. An access token lasts an hour
and a refresh token thirty days, replaced each time it is used. The same check
also accepts a personal key (see accounts.py), so an agent that can send a
header needs none of the dance above.
"""

from __future__ import annotations

import secrets
from typing import Any

from mcp.server.auth.provider import AccessToken, AuthorizationCode, AuthorizationParams, RefreshToken, TokenError, construct_redirect_uri
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from .accounts import TOKEN_PREFIX, Accounts, User, _digest
from .domain import new_id, now
from .store import Store

ACCESS_SECONDS = 3600
REFRESH_SECONDS = 30 * 86400
CODE_SECONDS = 300
REQUEST_SECONDS = 600


class Grants:
    """The authorization server's memory. Implements `OAuthAuthorizationServerProvider`."""

    def __init__(self, store: Store, accounts: Accounts, public_url: str) -> None:
        self.store, self.accounts, self.public_url = store, accounts, public_url.rstrip("/")

    # ------------------------------------------------------------------ clients

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        doc = await self.store.get("oauth_client", client_id)
        return OAuthClientInformationFull(**doc) if doc else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        assert client_info.client_id is not None
        await self.store.put_now("oauth_client", client_info.client_id, client_info.model_dump(mode="json"), created=now())

    # ------------------------------------------------------------------ asking the person

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        """The connector's request is valid. Park it and send the person to the console to answer it."""
        request_id = new_id("req")
        doc = {"client_id": client.client_id, "params": params.model_dump(mode="json"), "expires_at": now() + REQUEST_SECONDS}
        await self.store.put_now("oauth_request", request_id, doc, created=now())
        return f"{self.public_url}/app/authorize?request={request_id}"

    async def pending(self, request_id: str) -> dict[str, Any] | None:
        """What the consent page shows: who is asking, and where the answer will be sent."""
        doc = await self.store.get("oauth_request", request_id)
        if doc is None or doc["expires_at"] < now():
            return None
        client = await self.get_client(doc["client_id"])
        if client is None:
            return None
        params = AuthorizationParams(**doc["params"])
        return {"client_name": client.client_name or "An MCP client", "client_uri": str(client.client_uri or ""), "redirect_host": params.redirect_uri.host or ""}

    async def answer(self, request_id: str, user: User, approve: bool) -> str | None:
        """Record the person's answer once; returns where to send their browser, or None if the request is gone."""
        doc = await self.store.get("oauth_request", request_id)
        if doc is None or doc["expires_at"] < now():
            return None
        await self.store.delete("oauth_request", request_id)  # a request is answered once
        params = AuthorizationParams(**doc["params"])
        if not approve:
            return construct_redirect_uri(str(params.redirect_uri), error="access_denied", error_description="The person declined.", state=params.state)
        code = secrets.token_urlsafe(32)
        grant = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=now() + CODE_SECONDS,
            client_id=doc["client_id"],
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject=user.id,
        )
        await self.store.put_now("oauth_code", _digest(code), grant.model_dump(mode="json"), created=now())
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    # ------------------------------------------------------------------ codes and tokens

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        doc = await self.store.get("oauth_code", _digest(authorization_code))
        if doc is None or doc["client_id"] != client.client_id:
            return None
        return AuthorizationCode(**doc)

    async def _issue(self, *, client_id: str, scopes: list[str], resource: str | None, subject: str | None) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        shared = {"client_id": client_id, "scopes": scopes, "resource": resource, "subject": subject}
        await self.store.put_now("oauth_access", _digest(access), {**shared, "token": "", "expires_at": int(now()) + ACCESS_SECONDS}, parent=subject or "", created=now())
        await self.store.put_now("oauth_refresh", _digest(refresh), {**shared, "token": "", "expires_at": int(now()) + REFRESH_SECONDS, "access": _digest(access)}, parent=subject or "", created=now())
        return OAuthToken(access_token=access, expires_in=ACCESS_SECONDS, scope=" ".join(scopes) or None, refresh_token=refresh)

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        key = _digest(authorization_code.code)
        if await self.store.get("oauth_code", key) is None:
            raise TokenError("invalid_grant", "This code has already been used.")
        await self.store.delete("oauth_code", key)  # a code is exchanged once
        assert client.client_id is not None
        return await self._issue(client_id=client.client_id, scopes=authorization_code.scopes, resource=authorization_code.resource, subject=authorization_code.subject)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        doc = await self.store.get("oauth_refresh", _digest(refresh_token))
        if doc is None or doc["client_id"] != client.client_id:
            return None
        return RefreshToken(**{**doc, "token": refresh_token})

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        key = _digest(refresh_token.token)
        doc = await self.store.get("oauth_refresh", key)
        if doc is None:
            raise TokenError("invalid_grant", "This refresh token has already been used.")
        if refresh_token.subject and await self.accounts.user(refresh_token.subject) is None:
            raise TokenError("invalid_grant", "The person this token was issued to is no longer on the team.")
        await self.store.delete("oauth_refresh", key)  # rotated: the old pair stops working
        await self.store.delete("oauth_access", doc.get("access", ""))
        assert client.client_id is not None
        return await self._issue(client_id=client.client_id, scopes=scopes or refresh_token.scopes, resource=refresh_token.resource, subject=refresh_token.subject)

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Who a bearer token is, for the MCP endpoint: a connector that signed someone in, or a personal key."""
        if token.startswith(TOKEN_PREFIX):
            user = await self.accounts.token_user(token)
            return AccessToken(token=token, client_id="personal-key", scopes=[], subject=user.id) if user else None
        doc = await self.store.get("oauth_access", _digest(token))
        if doc is None or doc["expires_at"] < now():
            return None
        if doc.get("subject") and await self.accounts.user(doc["subject"]) is None:
            return None  # removed from the team: what they connected stops working
        return AccessToken(**{**doc, "token": token})

    async def access_user(self, token: str) -> User | None:
        """The same check for the HTTP API: the person behind a connector's token."""
        grant = await self.load_access_token(token)
        return await self.accounts.user(grant.subject) if grant and grant.subject else None

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        for kind in ("oauth_access", "oauth_refresh"):
            await self.store.delete(kind, _digest(token.token))

    async def exchange_identity_assertion(self, client: OAuthClientInformationFull, params: Any) -> OAuthToken:
        raise TokenError("unsupported_grant_type", "This server signs people in itself; it does not accept identity assertions.")
