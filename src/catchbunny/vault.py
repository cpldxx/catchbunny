"""Secrets at rest. Test-account passwords are encrypted before they reach
SQLite and decrypted only in memory, when a run needs to sign in.

The key comes from `CATCHBUNNY_SECRET_KEY` (a Fernet key) or, failing that, from a
key file created next to the database with owner-only permissions. Copying the
database alone therefore does not reveal a password.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "enc:v1:"


class Vault:
    def __init__(self, data_dir: Path) -> None:
        key = os.environ.get("CATCHBUNNY_SECRET_KEY", "").encode()
        if not key:
            path = Path(data_dir) / "secret.key"
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch(mode=0o600)
                path.write_bytes(Fernet.generate_key())
            key = path.read_bytes().strip()
        self._fernet = Fernet(key)

    def seal(self, secret: str) -> str:
        return secret if secret.startswith(PREFIX) else PREFIX + self._fernet.encrypt(secret.encode()).decode()

    def open(self, stored: str) -> str:
        if not stored.startswith(PREFIX):
            return stored  # written before encryption existed, or by a test
        try:
            return self._fernet.decrypt(stored[len(PREFIX) :].encode()).decode()
        except InvalidToken as exc:
            raise ValueError("a stored credential cannot be decrypted: the secret key changed") from exc
