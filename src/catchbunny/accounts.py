"""People and their keys: what turns a tool on one desk into one a team shares.

Catchbunny on your own machine has no accounts: it listens on localhost and
whoever can reach localhost is you. Served to a team (`catchbunny start --team`)
it needs to know who is asking, so this module keeps:

    the team       one per server: its name and the code a colleague joins with
    users          an owner (whoever set the server up) and members
    sessions       a browser that signed in, as a cookie
    tokens         a key a person hands to a coding agent or to CI

Nothing here is clever, on purpose. Passwords are stretched with scrypt and
never stored. Every secret the server hands out (a session, a token, the setup
code) is kept only as a hash, so reading the database does not let you in.

Stretching is the one expensive thing a stranger can ask for, so it is kept
off the event loop, a few at a time, and only after the guessing limits have
been checked: for an email address, and for the address the request came from.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import re
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field

from .domain import new_id, now
from .store import Store

SESSION_DAYS = 30
MIN_PASSWORD = 10
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I: a code is read out loud and typed by hand
_SCRYPT = {"n": 2**15, "r": 8, "p": 1}
TOKEN_PREFIX = "cb_"  # so a leaked key is recognisable in a log or a diff
STRETCHING_AT_ONCE = 4  # scrypt holds 32 MB and a core for about 60 ms; a flood of sign-ins queues for these
# What a password is checked against when there is no such account, so that answer costs what a real one
# does and does not tell a stranger which emails have accounts. Random: no password matches it.
_NOBODY = "scrypt${n}${r}${p}${salt}${key}".format(**_SCRYPT, salt=base64.b64encode(secrets.token_bytes(16)).decode(), key=base64.b64encode(secrets.token_bytes(32)).decode())


T = TypeVar("T")


class AccountError(Exception):
    """Something a person can fix: the message is shown to them as it is."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.message, self.status = message, status


class User(BaseModel):
    id: str = Field(default_factory=lambda: new_id("usr"))
    email: str
    name: str
    role: Literal["owner", "member"] = "member"
    password: str = ""  # scrypt, never the password
    created_at: float = Field(default_factory=now)

    def public(self) -> dict[str, Any]:
        return self.model_dump(exclude={"password"})


class Team(BaseModel):
    name: str = "Team"
    code: str  # what a colleague types to join; the owner can replace it at any time
    created_at: float = Field(default_factory=now)


class Token(BaseModel):
    """A key for something that is not a browser: a coding agent, a CI job."""

    id: str = Field(default_factory=lambda: new_id("tok"))
    user_id: str
    label: str
    hint: str  # the last four characters, to tell keys apart in a list
    created_at: float = Field(default_factory=now)
    last_used_at: float = 0.0


def new_code() -> str:
    return "-".join("".join(secrets.choice(_CODE_ALPHABET) for _ in range(4)) for _ in range(3))


def _digest(secret: str) -> str:
    """How a secret the server issued is stored and looked up. These are long random strings, so a plain hash is enough."""
    return hashlib.sha256(secret.encode()).hexdigest()[:40]


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.strip().upper().encode(), b.strip().upper().encode())


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, dklen=32, maxmem=64 * 1024 * 1024, **_SCRYPT)
    return "scrypt${n}${r}${p}${salt}${key}".format(**_SCRYPT, salt=base64.b64encode(salt).decode(), key=base64.b64encode(key).decode())


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, key = stored.split("$")
        if scheme != "scrypt":
            return False
        made = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32, maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(made, base64.b64decode(key))
    except (ValueError, TypeError):
        return False


class Accounts:
    WINDOW = 300.0  # seconds a wrong guess is remembered
    PER_EMAIL = 8  # wrong guesses at one account in that time
    PER_CLIENT = 30  # wrong guesses from one address, at any accounts: an office shares one address

    def __init__(self, store: Store, data_dir: Path) -> None:
        self.store, self.data_dir = store, Path(data_dir)
        self._failures: dict[str, list[float]] = {}  # who has been guessing, and when
        self._stretching = asyncio.Semaphore(STRETCHING_AT_ONCE)

    # ------------------------------------------------------------------ the team

    async def users(self) -> list[User]:
        return [User(**d) for d in await self.store.list("user")]

    async def user(self, user_id: str) -> User | None:
        doc = await self.store.get("user", user_id)
        return User(**doc) if doc else None

    async def needs_owner(self) -> bool:
        return not await self.store.list("user", limit=1)

    async def team(self) -> Team:
        doc = await self.store.get("team", "team")
        if doc:
            return Team(**doc)
        team = Team(code=new_code())
        await self.store.put_now("team", "team", team, created=team.created_at)
        return team

    async def rename_team(self, name: str) -> Team:
        team = (await self.team()).model_copy(update={"name": name.strip()[:60] or "Team"})
        await self.store.put_now("team", "team", team, created=team.created_at)
        return team

    async def new_team_code(self) -> Team:
        """Replace the code. People who already joined stay; the old code stops working."""
        team = (await self.team()).model_copy(update={"code": new_code()})
        await self.store.put_now("team", "team", team, created=team.created_at)
        return team

    def setup_code(self) -> str:
        """The code that proves you are the person who started the server, not just someone who can reach it.

        Shown in the terminal, kept in the data directory so a restart shows the same one, and gone
        once the owner exists.
        """
        path = self.data_dir / "setup_code"
        if path.exists() and (code := path.read_text().strip()):
            return code
        self.data_dir.mkdir(parents=True, exist_ok=True)
        code = new_code()
        path.write_text(code + "\n")
        path.chmod(0o600)
        return code

    # ------------------------------------------------------------------ joining and signing in

    async def _create(self, *, email: str, name: str, password: str, role: Literal["owner", "member"]) -> User:
        email, name = email.strip().lower(), name.strip()
        if not _EMAIL.match(email):
            raise AccountError("Enter an email address, like you@company.com.")
        if not name:
            raise AccountError("Enter your name.")
        if len(password) < MIN_PASSWORD:
            raise AccountError(f"Choose a password of at least {MIN_PASSWORD} characters.")
        if any(u.email == email for u in await self.users()):
            raise AccountError("There is already an account with that email. Sign in instead.", 409)
        user = User(email=email, name=name[:80], role=role, password=await self._stretch(hash_password, password))
        await self.store.put_now("user", user.id, user, created=user.created_at)
        return user

    async def create_owner(self, code: str, *, email: str, name: str, password: str, client: str = "") -> User:
        if not await self.needs_owner():
            raise AccountError("This server already has an owner. Ask them for the team code and join with it.", 409)
        guessers = self._guessers(client=client)
        self._slow_down(guessers)
        if not _same(code, self.setup_code()):
            self._failed(guessers)
            raise AccountError("That is not the setup code. It is printed in the terminal where the server was started.", 403)
        user = await self._create(email=email, name=name, password=password, role="owner")
        (self.data_dir / "setup_code").unlink(missing_ok=True)
        return user

    async def join(self, code: str, *, email: str, name: str, password: str, client: str = "") -> User:
        if await self.needs_owner():
            raise AccountError("This server has not been set up yet. Whoever started it sets it up first.", 409)
        guessers = self._guessers(f"join:{email.strip().lower()}", client)
        self._slow_down(guessers)
        if not _same(code, (await self.team()).code):
            self._failed(guessers)
            raise AccountError("That is not this team's code. Ask the owner for the current one.", 403)
        return await self._create(email=email, name=name, password=password, role="member")

    async def sign_in(self, email: str, password: str, *, client: str = "") -> User:
        email = email.strip().lower()
        guessers = self._guessers(f"sign-in:{email}", client)
        user = next((u for u in await self.users() if u.email == email), None)
        # Checked even when there is no such account, against a stand-in, so the answer takes as long either way.
        ok = await self._stretch(check_password, password, user.password if user else _NOBODY, before=lambda: self._slow_down(guessers))
        if user is None or not ok:
            self._failed(guessers)
            raise AccountError("The email or the password is wrong.", 401)
        return user

    async def remove(self, user_id: str, by: User) -> None:
        if by.role != "owner":
            raise AccountError("Only the owner can remove someone.", 403)
        if user_id == by.id:
            raise AccountError("The owner cannot remove themself.", 409)
        if await self.store.get("user", user_id) is None:
            raise AccountError("There is no such person on this team.", 404)
        for kind in ("session", "token"):
            for doc in await self.store.list(kind, parent=user_id, limit=10_000):
                await self.store.delete(kind, doc["key"])
        await self.store.delete("user", user_id)

    async def _stretch(self, work: Callable[..., T], *args: Any, before: Callable[[], None] | None = None) -> T:
        """Run scrypt in a thread, a few at a time. `before` runs once a turn has come: a burst sent at
        once waits here, and is refused here once the guessing limit is reached, without stretching."""
        async with self._stretching:
            if before:
                before()
            return await asyncio.to_thread(work, *args)

    def _guessers(self, account: str = "", client: str = "") -> list[tuple[str, int]]:
        """Who a wrong guess counts against: the account guessed at, and where the guess came from."""
        return [(key, limit) for key, limit in ((account, self.PER_EMAIL), (client and f"from:{client}", self.PER_CLIENT)) if key]

    def _slow_down(self, guessers: list[tuple[str, int]]) -> None:
        for who, limit in guessers:
            recent = [t for t in self._failures.get(who, []) if time.monotonic() - t < self.WINDOW]
            self._failures[who] = recent
            if len(recent) >= limit:
                raise AccountError("Too many attempts. Wait a few minutes and try again.", 429)

    def _failed(self, guessers: list[tuple[str, int]]) -> None:
        at = time.monotonic()
        for who, _ in guessers:
            self._failures.setdefault(who, []).append(at)
        if len(self._failures) > 10_000:  # a guess at a new address each time must not grow this for ever
            self._failures = {who: ts for who, ts in self._failures.items() if ts and at - ts[-1] < self.WINDOW}

    # ------------------------------------------------------------------ sessions (a browser)

    async def open_session(self, user: User) -> str:
        secret = secrets.token_urlsafe(32)
        key = _digest(secret)
        await self.store.put_now("session", key, {"key": key, "user_id": user.id, "expires_at": now() + SESSION_DAYS * 86400}, parent=user.id, created=now())
        return secret

    async def session_user(self, secret: str) -> User | None:
        if not secret:
            return None
        doc = await self.store.get("session", _digest(secret))
        if doc is None or doc["expires_at"] < now():
            return None
        return await self.user(doc["user_id"])

    async def close_session(self, secret: str) -> None:
        if secret:
            await self.store.delete("session", _digest(secret))

    # ------------------------------------------------------------------ tokens (an agent, a CI job)

    async def create_token(self, user: User, label: str) -> tuple[Token, str]:
        """A new key. The secret is returned once and cannot be shown again."""
        secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
        token = Token(user_id=user.id, label=label.strip()[:60] or "Unnamed key", hint=secret[-4:])
        key = _digest(secret)
        await self.store.put_now("token", key, {"key": key, **token.model_dump()}, parent=user.id, created=token.created_at)
        return token, secret

    async def tokens(self, user: User) -> list[Token]:
        return [Token(**d) for d in await self.store.list("token", parent=user.id)]

    async def revoke_token(self, token_id: str, user: User) -> None:
        doc = next((d for d in await self.store.list("token", parent=user.id) if d["id"] == token_id), None)
        if doc is None:
            raise AccountError("There is no such key.", 404)
        await self.store.delete("token", doc["key"])

    async def token_user(self, secret: str) -> User | None:
        """Whose key this is, or None. Notes when it was last used, at most once a minute."""
        if not secret.startswith(TOKEN_PREFIX):
            return None
        doc = await self.store.get("token", _digest(secret))
        if doc is None:
            return None
        if now() - doc.get("last_used_at", 0) > 60:
            self.store.put("token", doc["key"], {**doc, "last_used_at": now()}, parent=doc["user_id"], created=doc["created_at"])
        return await self.user(doc["user_id"])
