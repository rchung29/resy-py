import time

from shared.logger import get_logger
from shared.models import ProxyConfig

log = get_logger("checkout_pool")

DEFAULT_COOLDOWN_S = 300  # 5 minutes


class CheckoutPool:
    def __init__(self, proxies: list[ProxyConfig]):
        self._proxies = proxies
        self._index = 0
        self._cooldown: dict[str, float] = {}  # url -> expiry timestamp

    def get_next(self) -> str | None:
        """Round-robin, skipping proxies in cooldown."""
        if not self._proxies:
            return None

        now = time.time()
        self._cooldown = {u: e for u, e in self._cooldown.items() if e > now}

        available = [p for p in self._proxies if p.url not in self._cooldown]
        if not available:
            log.warning("all_checkout_proxies_in_cooldown")
            return None

        idx = self._index % len(available)
        self._index = idx + 1
        return available[idx].url

    def mark_bad(self, url: str, cooldown_s: float = DEFAULT_COOLDOWN_S) -> None:
        self._cooldown[url] = time.time() + cooldown_s
        log.warning("proxy_marked_bad", url=url, cooldown_s=cooldown_s)

    def reset(self) -> None:
        self._index = 0
        self._cooldown.clear()
