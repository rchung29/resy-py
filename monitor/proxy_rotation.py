import time

from shared.models import ProxyConfig
from shared.logger import get_logger

log = get_logger("proxy_rotation")

# Default rate limit cooldown: 15 minutes
DEFAULT_RATE_LIMIT_S = 15 * 60


class ProxyRotator:
    def __init__(self, proxies: list[ProxyConfig]):
        self._proxies = proxies
        self._index = 0
        self._rate_limited: dict[str, float] = {}  # url -> expiry timestamp

    def get_next(self) -> str | None:
        """Get next available proxy via round-robin, skipping rate-limited ones."""
        if not self._proxies:
            return None

        now = time.time()
        n = len(self._proxies)

        # Walk through the full list starting from current index,
        # return the first proxy that isn't rate-limited
        for _ in range(n):
            idx = self._index % n
            self._index += 1
            proxy = self._proxies[idx]

            expiry = self._rate_limited.get(proxy.url)
            if expiry and expiry > now:
                continue

            # Clean up expired entry
            if expiry:
                del self._rate_limited[proxy.url]

            return proxy.url

        log.warning("all_proxies_rate_limited")
        return None

    def mark_rate_limited(
        self, url: str, duration_s: float = DEFAULT_RATE_LIMIT_S
    ) -> None:
        self._rate_limited[url] = time.time() + duration_s
        log.warning("proxy_rate_limited", url=url, duration_s=duration_s)

    @property
    def available_count(self) -> int:
        now = time.time()
        return sum(
            1 for p in self._proxies if p.url not in self._rate_limited or self._rate_limited[p.url] <= now
        )

    def reset(self) -> None:
        self._index = 0
        self._rate_limited.clear()
