"""Auth cache — manages login credentials with automatic refresh."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from shared.logger import get_logger
from shared.models import UserConfig
from utils.auth import login

log = get_logger("auth_cache")


@dataclass
class AuthCredentials:
    auth_token: str
    payment_method_id: int
    expires_at: float  # time.time() + TTL


class AuthCache:
    TTL_S = 21600  # 2 hours

    def __init__(self, api_key: str, proxy_url: str | None = None):
        self._cache: dict[str, AuthCredentials] = {}  # user_id -> creds
        self._users: dict[str, UserConfig] = {}  # user_id -> config
        self._api_key = api_key
        self._proxy_url = proxy_url
        self._locks: dict[str, asyncio.Lock] = {}  # per-user locks

    def register_users(self, users: list[UserConfig]) -> None:
        for user in users:
            self._users[user.id] = user
            self._locks[user.id] = asyncio.Lock()

    async def get_credentials(self, user_id: str) -> AuthCredentials | None:
        """Get cached creds, or login to refresh if expired/missing."""
        cached = self._cache.get(user_id)
        if cached and cached.expires_at > time.time():
            return cached

        lock = self._locks.get(user_id)
        if not lock:
            log.error("unknown_user", user_id=user_id)
            return None

        async with lock:
            # Double-check after acquiring lock
            cached = self._cache.get(user_id)
            if cached and cached.expires_at > time.time():
                return cached

            return await self._login_user(user_id)

    async def invalidate(self, user_id: str) -> None:
        """Force refresh on next access (called on 419 error)."""
        self._cache.pop(user_id, None)
        log.info("credentials_invalidated", user_id=user_id)

    async def warm_all(self) -> None:
        """Login all users at startup. Log failures but don't block."""
        results = await asyncio.gather(
            *[self._login_user(uid) for uid in self._users],
            return_exceptions=True,
        )

        success = sum(1 for r in results if r and not isinstance(r, Exception))
        failed = len(results) - success
        log.info("auth_cache_warmed", success=success, failed=failed)

    async def _login_user(self, user_id: str) -> AuthCredentials | None:
        """Login a single user and cache the result."""
        user = self._users.get(user_id)
        if not user:
            return None

        try:
            result = await login(
                email=user.email,
                password=user.password,
                proxy_url=self._proxy_url,
                api_key=self._api_key,
            )

            creds = AuthCredentials(
                auth_token=result["auth_token"],
                payment_method_id=result["payment_method_id"],
                expires_at=time.time() + self.TTL_S,
            )
            self._cache[user_id] = creds
            log.info("credentials_cached", user_id=user_id, ttl_s=self.TTL_S)
            return creds

        except Exception as e:
            log.error("login_failed", user_id=user_id, error=str(e))
            return None
