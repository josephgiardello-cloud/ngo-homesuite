from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass

from ..auth.passwords import encode_password, verify_password
from ..prompts import utc_now_iso
from .services import Actor, Services, inserted_id, text
from .store import DomainError


@dataclass(frozen=True)
class Session:
    actor: Actor
    csrf: str
    token: str


class Identity:
    def __init__(self, services: Services) -> None:
        self.services = services
        self._dummy_salt, self._dummy_hash = encode_password(secrets.token_urlsafe(32))

    def create_user(
        self, tenant: str, username: str, password: str, role: str, *, actor: Actor | None = None,
    ) -> int:
        username = text(username, "Username", 100)
        if role not in {"admin", "fundraiser", "viewer"}:
            raise DomainError("Invalid role")
        try:
            salt, pw_hash = encode_password(password)
        except ValueError as exc:
            raise DomainError(str(exc)) from exc
        with self.services.store.transaction(tenant, write=True) as conn:
            if actor is None:
                if role != "admin" or conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                    raise DomainError("Bootstrap is allowed only for the first admin", 403)
            else:
                if actor.tenant != tenant:
                    raise DomainError("Organization mismatch", 403)
                self.services.authorize(conn, actor, "admin")
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                raise DomainError("Username already exists", 409)
            cursor = conn.execute(
                "INSERT INTO users(username, password_salt, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
                (username, salt, pw_hash, role, utc_now_iso()),
            )
            user_id = inserted_id(cursor)
            self.services.event(conn, actor or Actor(tenant, user_id), "user.create", "user", user_id,
                                {"username": username, "role": role, "bootstrap": actor is None})
            return user_id

    def login(self, tenant: str, username: str, password: str) -> Session:
        username = text(username, "Username", 100)
        if len(password) > 1024:
            raise DomainError("Invalid credentials", 401)
        now = int(time.time())
        success = False
        blocked = False
        token, csrf = f"{tenant}.{secrets.token_urlsafe(32)}", secrets.token_urlsafe(32)
        user_id = 0
        with self.services.store.transaction(tenant, write=True) as conn:
            attempt = conn.execute("SELECT * FROM login_attempts WHERE username = ?", (username,)).fetchone()
            blocked = attempt is not None and attempt["retry_after"] > now
            if not blocked:
                user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
                valid = verify_password(
                    password, user["password_salt"] if user else self._dummy_salt,
                    user["password_hash"] if user else self._dummy_hash,
                )
                success = valid and user is not None and user["role"] in {"admin", "fundraiser", "viewer"}
                if success:
                    user_id = user["id"]
                    conn.execute("DELETE FROM login_attempts WHERE username = ?", (username,))
                    conn.execute(
                        "INSERT INTO web_sessions(token_hash, user_id, csrf_token, expires_at) VALUES (?, ?, ?, ?)",
                        (hashlib.sha256(token.encode()).hexdigest(), user_id, csrf, now + 8 * 3600),
                    )
                else:
                    failures = (attempt["failures"] if attempt else 0) + 1
                    conn.execute(
                        "INSERT OR REPLACE INTO login_attempts VALUES (?, ?, ?)",
                        (username, failures, now + min(900, 2 ** min(failures, 10))),
                    )
        # Failed attempts commit before reporting failure, so retries cannot erase the backoff.
        if blocked:
            raise DomainError("Login temporarily locked; try again later", 429)
        if not success:
            raise DomainError("Invalid credentials", 401)
        return Session(Actor(tenant, user_id), csrf, token)

    def resolve(self, token: str) -> Session:
        if "." not in token or len(token) > 256:
            raise DomainError("Authentication required", 401)
        tenant = token.split(".", 1)[0]
        try:
            with self.services.store.transaction(tenant) as conn:
                row = conn.execute(
                    "SELECT * FROM web_sessions WHERE token_hash = ? AND expires_at > ?",
                    (hashlib.sha256(token.encode()).hexdigest(), int(time.time())),
                ).fetchone()
                if row is None:
                    raise DomainError("Authentication required", 401)
                actor = Actor(tenant, row["user_id"])
                self.services.authorize(conn, actor, "admin", "fundraiser", "viewer")
                return Session(actor, row["csrf_token"], token)
        except DomainError as exc:
            if exc.status == 404:
                raise DomainError("Authentication required", 401) from exc
            raise

    def logout(self, session: Session) -> None:
        with self.services.store.transaction(session.actor.tenant, write=True) as conn:
            conn.execute("DELETE FROM web_sessions WHERE token_hash = ?",
                         (hashlib.sha256(session.token.encode()).hexdigest(),))
