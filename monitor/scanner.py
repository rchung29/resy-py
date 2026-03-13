from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

from monitor.proxy_rotation import DEFAULT_RATE_LIMIT_S
from sdk.client import ResyClient
from sdk.errors import ResyAPIError
from shared.filters import filter_slots
from shared.logger import get_logger
from shared.models import DiscoveredSlotsMessage, RestaurantConfig
from shared.slots import parse_find_slots_response

if TYPE_CHECKING:
    from monitor.scheduler import ReleaseWindow

log = get_logger("scanner")


@dataclass
class ScanStats:
    total_iterations: int = 0
    total_slots_found: int = 0
    restaurants_with_slots: int = 0
    restaurants_without_slots: int = 0
    elapsed_ms: float = 0


class Scanner:
    def __init__(
        self,
        api_key: str,
        scan_interval_ms: int = 1000,
        scan_timeout_seconds: int = 120,
        proxy_rotator=None,
        use_proxies: bool = True,
        on_slots_discovered: Callable[[DiscoveredSlotsMessage], Awaitable[None]] | None = None,
    ):
        self._api_key = api_key
        self._scan_interval_ms = scan_interval_ms
        self._scan_timeout_s = scan_timeout_seconds
        self._proxy_rotator = proxy_rotator
        self._use_proxies = use_proxies
        self._on_slots_discovered = on_slots_discovered
        self._active_scans: dict[str, bool] = {}
        self._completed_restaurants: set[str] = set()
        self._total_slots_found = 0

    async def start_scan(self, window: ReleaseWindow) -> ScanStats:
        """Start independent scan loops per restaurant, each on its own cadence."""
        key = window.id
        if self._active_scans.get(key):
            log.warning("scan_already_active", window=key)
            return ScanStats()

        self._completed_restaurants.clear()
        self._total_slots_found = 0
        self._active_scans[key] = True

        start = time.time()

        log.info(
            "scan_starting",
            window=key,
            restaurants={r.name: window.target_date_for(r.venue_id) for r in window.restaurants},
            interval_ms=self._scan_interval_ms,
            timeout_s=self._scan_timeout_s,
        )

        # Each restaurant gets its own polling loop
        tasks = [
            asyncio.create_task(
                self._scan_restaurant_loop(r, window.target_date_for(r.venue_id), key)
            )
            for r in window.restaurants
        ]

        # Wait for all to finish (they self-terminate on timeout or slot found)
        await asyncio.gather(*tasks, return_exceptions=True)
        self._active_scans.pop(key, None)

        elapsed_ms = (time.time() - start) * 1000
        stats = ScanStats(
            total_slots_found=self._total_slots_found,
            restaurants_with_slots=len(self._completed_restaurants),
            restaurants_without_slots=len(window.restaurants) - len(self._completed_restaurants),
            elapsed_ms=elapsed_ms,
        )
        log.info("scan_complete", **stats.__dict__)
        return stats

    async def _scan_restaurant_loop(
        self, restaurant: RestaurantConfig, target_date: str, scan_key: str
    ) -> None:
        """Independent polling loop for a single restaurant."""
        start = time.time()
        timeout_ms = self._scan_timeout_s * 1000
        iteration = 0

        while self._active_scans.get(scan_key):
            elapsed = (time.time() - start) * 1000
            if elapsed > timeout_ms:
                log.debug("restaurant_scan_timeout", restaurant=restaurant.name)
                break

            if restaurant.venue_id in self._completed_restaurants:
                break

            iteration += 1
            await self._scan_restaurant(restaurant, target_date)

            # Sleep with jitter before next poll
            jitter = random.randint(-500, 500)
            sleep_ms = max(0, self._scan_interval_ms + jitter)
            await asyncio.sleep(sleep_ms / 1000)

            if iteration % 10 == 0:
                log.debug(
                    "restaurant_scan_progress",
                    restaurant=restaurant.name,
                    iteration=iteration,
                    elapsed_ms=round((time.time() - start) * 1000),
                )

    async def _scan_restaurant(
        self, restaurant: RestaurantConfig, target_date: str
    ) -> None:
        """Scan a single restaurant, filter slots, emit if any match."""
        proxy_url = None
        if self._use_proxies and self._proxy_rotator:
            proxy_url = self._proxy_rotator.get_next()

        client = ResyClient(
            api_key=self._api_key,
            proxy_url=proxy_url,
        )

        try:
            response = await client.find_slots(
                venue_id=int(restaurant.venue_id),
                day=target_date,
                party_size=restaurant.party_size,
            )

            venue_name, slot_datas = parse_find_slots_response(response)
            if not slot_datas:
                return
            venue_name = venue_name or restaurant.name

            log.info(
                "raw_slots",
                restaurant=restaurant.name,
                party_size=restaurant.party_size,
                count=len(slot_datas),
            )

            # Filter by day_configs time windows
            matched = filter_slots(slot_datas, restaurant.day_configs, target_date)
            if not matched:
                return

            # Mark complete, emit
            self._completed_restaurants.add(restaurant.venue_id)
            self._total_slots_found += len(matched)

            msg = DiscoveredSlotsMessage(
                venue_id=restaurant.venue_id,
                restaurant_name=venue_name,
                target_date=target_date,
                party_size=restaurant.party_size,
                slots=matched,
            )

            log.info(
                "slots_discovered",
                restaurant=restaurant.name,
                count=len(matched),
                slots=[{"time": s.time, "type": s.type} for s in matched],
            )

            if self._on_slots_discovered:
                asyncio.create_task(self._on_slots_discovered(msg))

        except ResyAPIError as e:
            is_waf = e.status == 500 and (not e.raw_body or e.raw_body.strip() == "")
            should_rotate = e.status in (429, 502) or is_waf

            if should_rotate and self._use_proxies and self._proxy_rotator and proxy_url:
                cooldown = 8.0 if is_waf else DEFAULT_RATE_LIMIT_S
                self._proxy_rotator.mark_rate_limited(proxy_url, cooldown)

            if e.status == 429:
                log.warning("rate_limited", restaurant=restaurant.name)
            elif e.status == 502:
                log.warning("bad_gateway", restaurant=restaurant.name)
            elif is_waf:
                log.warning("waf_blocked", restaurant=restaurant.name)
            else:
                log.error("api_error", restaurant=restaurant.name, status=e.status)
        except Exception as e:
            log.error("scan_error", restaurant=restaurant.name, error=str(e))
        finally:
            await client.close()

    def stop_all(self) -> None:
        for key in list(self._active_scans):
            self._active_scans[key] = False
        log.info("stopping_all_scans")
