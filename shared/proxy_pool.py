"""Unified proxy pool — random selection with in-use tracking and cooldowns."""

import random
import time

from shared.logger import get_logger
from shared.models import ProxyConfig

log = get_logger("proxy_pool")

DEFAULT_COOLDOWN_S = 300  # 5 minutes


class ProxyPool:
    def __init__(self, proxies: list[ProxyConfig]):
        self._proxies = proxies
        self._in_use: set[str] = set()
        self._cooldown: dict[str, float] = {}  # url -> expiry timestamp

    def acquire(self) -> str | None:
        """Pick a random available proxy (not in-use, not in cooldown). Returns None if empty."""
        if not self._proxies:
            return None

        now = time.time()
        # Clean expired cooldowns
        self._cooldown = {u: e for u, e in self._cooldown.items() if e > now}

        available = [
            p.url for p in self._proxies
            if p.url not in self._in_use and p.url not in self._cooldown
        ]

        if not available:
            # Fall back: allow in-use proxies (but still skip cooldown)
            available = [
                p.url for p in self._proxies
                if p.url not in self._cooldown
            ]

        if not available:
            log.warning("all_proxies_unavailable", total=len(self._proxies))
            return None

        url = random.choice(available)
        self._in_use.add(url)
        return url

    def release(self, url: str) -> None:
        """Mark proxy as no longer in use."""
        self._in_use.discard(url)

    def mark_bad(self, url: str, cooldown_s: float = DEFAULT_COOLDOWN_S) -> None:
        """Put proxy in cooldown (e.g. WAF blocked, rate limited)."""
        self._in_use.discard(url)
        self._cooldown[url] = time.time() + cooldown_s
        log.warning("proxy_cooldown", url=url, cooldown_s=cooldown_s)

    @property
    def available_count(self) -> int:
        now = time.time()
        return sum(
            1 for p in self._proxies
            if p.url not in self._in_use
            and (p.url not in self._cooldown or self._cooldown[p.url] <= now)
        )

    @property
    def total_count(self) -> int:
        return len(self._proxies)

    def reset(self) -> None:
        self._in_use.clear()
        self._cooldown.clear()
