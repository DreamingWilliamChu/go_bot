"""The sign-in account: one admin user with a password, and the browser sessions it opened.

Stored in ``data/account.json`` (mode 0600):

* the password as a salted scrypt hash, never in clear text;
* sessions as the sha256 of the random cookie value, so the file alone cannot be used to sign in.

Changing the password ends every other session. Failed sign-ins are rate limited per client and
overall, so a password exposed to the internet through the tunnel cannot be guessed quickly.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SESSION_COOKIE = "gobot_session"
SESSION_DAYS = 30
MIN_PASSWORD = 8
DEFAULT_USERNAME = "admin"

# scrypt cost: ~50 ms on an M1, 16 MB of memory.
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    b64 = base64.b64encode
    return f"scrypt${_N}${_R}${_P}${b64(salt).decode()}${b64(dk).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        want = base64.b64decode(dk)
        got = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(want),
            maxmem=64 * 1024 * 1024,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


def _session_key(value: str) -> str:
    return hashlib.sha256(("gobot-session:" + value).encode()).hexdigest()


class AccountError(ValueError):
    pass


@dataclass
class _Limiter:
    """Failed sign-ins: 5 free tries per client, then a lock that doubles up to 15 minutes."""

    per_client: dict[str, tuple[int, float]] = field(default_factory=dict)  # key -> (fails, locked_until)
    recent: list[float] = field(default_factory=list)  # all failures, last minute
    global_per_minute: int = 30

    def wait_seconds(self, key: str, now: float) -> float:
        self.recent = [t for t in self.recent if now - t < 60]
        if len(self.recent) >= self.global_per_minute:
            return 60 - (now - self.recent[0])
        _, until = self.per_client.get(key, (0, 0.0))
        return max(0.0, until - now)

    def failed(self, key: str, now: float) -> None:
        self.recent.append(now)
        fails, _ = self.per_client.get(key, (0, 0.0))
        fails += 1
        lock = 0.0 if fails < 5 else min(900.0, 30.0 * 2 ** (fails - 5))
        self.per_client[key] = (fails, now + lock)
        if len(self.per_client) > 10000:  # bounded memory under a spray of addresses
            self.per_client.clear()

    def succeeded(self, key: str) -> None:
        self.per_client.pop(key, None)


class AccountStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.limiter = _Limiter()
        self._data: dict[str, Any] = {}
        self._mtime: int | None = None
        self._refresh()

    # ------------------------------------------------------------------ state
    def _refresh(self) -> None:
        """Re-read the file if another process (``gobot account ...``) changed it."""
        try:
            mtime = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            mtime = None
        if mtime == self._mtime and self._data:
            return
        data: dict[str, Any] = {"username": DEFAULT_USERNAME, "password": None, "sessions": {}}
        if mtime is not None:
            try:
                loaded = json.loads(self.path.read_text())
                if isinstance(loaded, dict):
                    data.update(loaded)
            except (OSError, ValueError):
                return  # half-written by another process: keep what we have
        self._data, self._mtime = data, mtime

    @property
    def username(self) -> str:
        self._refresh()
        return str(self._data.get("username") or DEFAULT_USERNAME)

    @property
    def password_set(self) -> bool:
        self._refresh()
        return bool(self._data.get("password"))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self._data, f, indent=2)
            f.write("\n")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        self._mtime = self.path.stat().st_mtime_ns

    # ------------------------------------------------------------------ credentials
    def set_credentials(
        self, username: str | None, password: str | None, keep_session: str | None = None
    ) -> None:
        """Change the username and/or password. A new password ends all sessions but ``keep_session``."""
        with self._lock:
            self._refresh()
            if username is not None:
                username = username.strip()
                if not (1 <= len(username) <= 64) or any(c.isspace() for c in username):
                    raise AccountError("The username must be 1-64 characters without spaces.")
                self._data["username"] = username
            if password is not None:
                if len(password) < MIN_PASSWORD:
                    raise AccountError(f"The password must be at least {MIN_PASSWORD} characters.")
                if len(password) > 1024:
                    raise AccountError("The password is too long.")
                self._data["password"] = hash_password(password)
                keep = _session_key(keep_session) if keep_session else None
                self._data["sessions"] = {
                    k: v for k, v in self._data.get("sessions", {}).items() if k == keep
                }
            self._save()

    def check_password(self, password: str) -> bool:
        self._refresh()
        return verify_password(password, self._data.get("password"))

    def check_login(self, username: str, password: str) -> bool:
        # Always run scrypt so a wrong username takes as long as a wrong password.
        ok_pw = self.check_password(password)
        ok_user = hmac.compare_digest(username.strip().lower().encode(), self.username.lower().encode())
        return ok_pw and ok_user

    # ------------------------------------------------------------------ sessions
    def create_session(self, label: str = "") -> str:
        value = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._refresh()
            sessions = self._data.setdefault("sessions", {})
            sessions[_session_key(value)] = {
                "created": int(now),
                "expires": int(now + SESSION_DAYS * 86400),
                "label": label[:120],
            }
            # Drop expired ones while we are here.
            for k in [k for k, v in sessions.items() if v.get("expires", 0) < now]:
                del sessions[k]
            self._save()
        return value

    def valid_session(self, value: str | None) -> bool:
        if not value or not self.password_set:  # password_set re-reads the file if it changed
            return False
        s = self._data.get("sessions", {}).get(_session_key(value))
        return bool(s and s.get("expires", 0) > time.time())

    def end_session(self, value: str | None) -> None:
        if not value:
            return
        with self._lock:
            self._refresh()
            if self._data.get("sessions", {}).pop(_session_key(value), None) is not None:
                self._save()

    def end_all_sessions(self, keep_session: str | None = None) -> int:
        keep = _session_key(keep_session) if keep_session else None
        with self._lock:
            self._refresh()
            sessions = self._data.get("sessions", {})
            n = len([k for k in sessions if k != keep])
            self._data["sessions"] = {k: v for k, v in sessions.items() if k == keep}
            self._save()
        return n
