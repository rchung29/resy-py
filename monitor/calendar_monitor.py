"""Passive calendar monitor — polls for availability changes and triggers bookings."""

from __future__ import annotations

import asyncio
import random
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable

from zoneinfo import ZoneInfo

from sdk.client import ResyClient
from sdk.errors import ResyAPIError
from shared.filters import filter_slots
from shared.logger import get_logger
from shared.models import DiscoveredSlotsMessage, RestaurantConfig
from shared.proxy_pool import ProxyPool
from shared.slots import parse_find_slots_response

log = get_logger("calendar_monitor")

ET = ZoneInfo("America/New_York")

# Blackout windows: pause polling 10 min before/after these ET times
BLACKOUT_TIMES = ["00:00", "07:00", "09:00", "10:00", "12:00"]
BLACKOUT_MARGIN_MIN = 10


def _in_blackout() -> float | None:
    """Return seconds to sleep if currently in a blackout window, else None."""
    now = datetime.now(ET)
    for bt_str in BLACKOUT_TIMES:
        h, m = map(int, bt_str.split(":"))
        center = now.replace(hour=h, minute=m, second=0, microsecond=0)
        start = center - timedelta(minutes=BLACKOUT_MARGIN_MIN)
        end = center + timedelta(minutes=BLACKOUT_MARGIN_MIN)
        if start <= now <= end:
            remaining = (end - now).total_seconds() + 1
            return max(remaining, 1)
    return None


class CalendarMonitor:
    """Continuously polls calendar for a set of restaurants and triggers booking on new availability."""

    def __init__(
        self,
        restaurants: list[RestaurantConfig],
        api_key: str,
        poll_interval_s: int = 45,
        calendar_days: int = 30,
        proxy_pool: ProxyPool | None = None,
        on_slots_discovered: Callable[[DiscoveredSlotsMessage], Awaitable[None]] | None = None,
    ):
        self._restaurants = restaurants
        self._api_key = api_key
        self._poll_interval_s = poll_interval_s
        self._calendar_days = calendar_days
        self._proxy_pool = proxy_pool
        self._on_slots_discovered = on_slots_discovered
        self._running = False

        # venue_id -> {date_str: "available" | "not-available"}
        self._calendar_state: dict[str, dict[str, str]] = {}

    async def run(self) -> None:
        """Start polling tasks for all restaurants (staggered starts)."""
        self._running = True
        log.info(
            "passive_monitor_starting",
            restaurants=[r.name for r in self._restaurants],
            interval_s=self._poll_interval_s,
            calendar_days=self._calendar_days,
        )

        tasks = []
        for i, restaurant in enumerate(self._restaurants):
            # Stagger starts so we don't hit the API all at once
            stagger_s = i * 5
            tasks.append(asyncio.create_task(self._poll_loop(restaurant, stagger_s)))

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _poll_loop(self, restaurant: RestaurantConfig, stagger_s: int) -> None:
        """Polling loop for a single restaurant."""
        if stagger_s > 0:
            await asyncio.sleep(stagger_s)

        first_poll = True
        while self._running:
            # Check blackout
            blackout_sleep = _in_blackout()
            if blackout_sleep:
                log.debug("blackout_sleeping", restaurant=restaurant.name, sleep_s=round(blackout_sleep))
                await asyncio.sleep(blackout_sleep)
                continue

            try:
                await self._poll_calendar(restaurant, first_poll)
                first_poll = False
            except Exception as e:
                log.error("poll_unexpected_error", restaurant=restaurant.name, error=str(e))

            # Sleep with jitter
            jitter = random.uniform(-5, 5)
            sleep_s = max(5, self._poll_interval_s + jitter)
            await asyncio.sleep(sleep_s)

    async def _poll_calendar(self, restaurant: RestaurantConfig, first_poll: bool) -> None:
        """Poll calendar, diff state, and trigger find_slots for newly available dates."""
        proxy_url = self._proxy_pool.acquire() if self._proxy_pool else None

        client = ResyClient(api_key=self._api_key, proxy_url=proxy_url)
        try:
            today = date.today()
            start_date = today.isoformat()
            end_date = (today + timedelta(days=self._calendar_days)).isoformat()

            data = await client.get_calendar(
                venue_id=int(restaurant.venue_id),
                party_size=restaurant.party_size,
                start_date=start_date,
                end_date=end_date,
            )

            # Parse calendar: date -> availability status
            new_state: dict[str, str] = {}
            for entry in data.get("scheduled", []):
                d = entry.get("date")
                avail = entry.get("inventory", {}).get("reservation", "not-available")
                if d:
                    new_state[d] = avail

            old_state = self._calendar_state.get(restaurant.venue_id, {})

            if first_poll:
                # First poll: check all currently available dates
                newly_available = [d for d, status in new_state.items() if status == "available"]
                log.info(
                    "first_poll",
                    restaurant=restaurant.name,
                    total_dates=len(new_state),
                    available_dates=len(newly_available),
                )
            else:
                # Subsequent polls: find dates that flipped to available
                newly_available = [
                    d for d, status in new_state.items()
                    if status == "available" and old_state.get(d) != "available"
                ]
                if newly_available:
                    log.info(
                        "new_availability_detected",
                        restaurant=restaurant.name,
                        dates=newly_available,
                    )

            # Store state after determining diffs
            self._calendar_state[restaurant.venue_id] = new_state

            if not newly_available:
                return

            # Filter by day_configs weekday
            dates_to_check = self._filter_dates_by_day_configs(newly_available, restaurant)
            if not dates_to_check:
                log.debug(
                    "no_matching_weekdays",
                    restaurant=restaurant.name,
                    newly_available=newly_available,
                )
                return

            # For each matching date, call find_slots and emit
            for target_date in dates_to_check:
                await self._find_and_emit_slots(restaurant, target_date, client)

        except ResyAPIError as e:
            is_waf = e.status == 500 and (not e.raw_body or e.raw_body.strip() == "")

            if proxy_url and self._proxy_pool:
                if is_waf:
                    self._proxy_pool.mark_bad(proxy_url, cooldown_s=8.0)
                elif e.status in (429, 502):
                    self._proxy_pool.mark_bad(proxy_url, cooldown_s=60.0)

            if e.status == 429:
                log.warning("rate_limited", restaurant=restaurant.name)
            elif is_waf:
                log.warning("waf_blocked", restaurant=restaurant.name)
            else:
                log.error("api_error", restaurant=restaurant.name, status=e.status)
        finally:
            if proxy_url and self._proxy_pool:
                self._proxy_pool.release(proxy_url)
            await client.close()

    def _filter_dates_by_day_configs(
        self, dates: list[str], restaurant: RestaurantConfig
    ) -> list[str]:
        """Filter dates to only those whose weekday matches a day_config."""
        if not restaurant.day_configs:
            return dates  # No filtering = accept all

        allowed_weekdays = {dc.day for dc in restaurant.day_configs}
        return [
            d for d in dates
            if datetime.strptime(d, "%Y-%m-%d").weekday() in allowed_weekdays
        ]

    async def _find_and_emit_slots(
        self, restaurant: RestaurantConfig, target_date: str, client: ResyClient
    ) -> None:
        """Call find_slots for a date, filter, and emit DiscoveredSlotsMessage."""
        try:
            response = await client.find_slots(
                venue_id=int(restaurant.venue_id),
                day=target_date,
                party_size=restaurant.party_size,
            )

            venue_name, slot_datas = parse_find_slots_response(response)
            if not slot_datas:
                log.debug("no_slots_for_date", restaurant=restaurant.name, date=target_date)
                return
            venue_name = venue_name or restaurant.name

            log.info(
                "raw_slots",
                restaurant=restaurant.name,
                date=target_date,
                count=len(slot_datas),
            )

            matched = filter_slots(slot_datas, restaurant.day_configs, target_date)
            if not matched:
                return

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
                date=target_date,
                count=len(matched),
                slots=[{"time": s.time, "type": s.type} for s in matched],
            )

            if self._on_slots_discovered:
                asyncio.create_task(self._on_slots_discovered(msg))

        except ResyAPIError as e:
            log.warning(
                "find_slots_error",
                restaurant=restaurant.name,
                date=target_date,
                status=e.status,
            )
        except Exception as e:
            log.error(
                "find_slots_unexpected_error",
                restaurant=restaurant.name,
                date=target_date,
                error=str(e),
            )

    def stop(self) -> None:
        self._running = False
        log.info("passive_monitor_stopping")
