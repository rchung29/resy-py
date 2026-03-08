from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from shared.logger import get_logger
from shared.models import DayConfig, RestaurantConfig

log = get_logger("scheduler")


@dataclass
class ReleaseWindow:
    id: str
    release_time: str              # "HH:MM"
    release_time_zone: str
    release_datetime: datetime
    scan_start_datetime: datetime
    target_date: str               # "YYYY-MM-DD"
    restaurants: list[RestaurantConfig] = field(default_factory=list)


def calculate_target_date(
    days_in_advance: int,
    from_dt: datetime,
    timezone: str = "America/New_York",
) -> str:
    """Calculate the booking target date from a release datetime."""
    tz = ZoneInfo(timezone)
    local = from_dt.astimezone(tz)
    target = local + timedelta(days=days_in_advance)
    return target.strftime("%Y-%m-%d")


def get_next_release_datetime(
    release_time: str,
    timezone: str = "America/New_York",
    reference: datetime | None = None,
) -> datetime:
    """Get the next occurrence of release_time. If it's passed today, returns tomorrow."""
    tz = ZoneInfo(timezone)
    now = (reference or datetime.now(tz)).astimezone(tz)

    h, m = map(int, release_time.split(":"))
    release = now.replace(hour=h, minute=m, second=0, microsecond=0)

    if release <= now:
        release += timedelta(days=1)

    return release


def is_active_for_date(day_configs: list[DayConfig], target_date: str) -> bool:
    """Check if any day_config matches the target date's weekday (Python native: 0=Mon..6=Sun)."""
    if not day_configs:
        return True  # No restrictions = always active
    weekday = datetime.strptime(target_date, "%Y-%m-%d").weekday()
    return any(dc.day == weekday for dc in day_configs)


def calculate_release_windows(
    restaurants: list[RestaurantConfig],
    scan_start_seconds_before: int = 45,
    reference: datetime | None = None,
) -> list[ReleaseWindow]:
    """Group restaurants by (release_time, timezone) into release windows."""
    window_map: dict[str, ReleaseWindow] = {}

    for restaurant in restaurants:
        if not restaurant.enabled:
            continue

        release_dt = get_next_release_datetime(
            restaurant.release_time,
            restaurant.release_time_zone,
            reference,
        )
        target_date = calculate_target_date(
            restaurant.days_in_advance,
            release_dt,
            restaurant.release_time_zone,
        )

        if not is_active_for_date(restaurant.day_configs, target_date):
            day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            weekday = datetime.strptime(target_date, "%Y-%m-%d").weekday()
            log.debug(
                "skipping_restaurant_day",
                restaurant=restaurant.name,
                target_date=target_date,
                day=day_names[weekday],
                configured_days=[day_names[dc.day] for dc in restaurant.day_configs],
            )
            continue

        window_id = f"{restaurant.release_time_zone}:{restaurant.release_time}:{target_date}"

        if window_id in window_map:
            window_map[window_id].restaurants.append(restaurant)
        else:
            scan_start = release_dt - timedelta(seconds=scan_start_seconds_before)
            window_map[window_id] = ReleaseWindow(
                id=window_id,
                release_time=restaurant.release_time,
                release_time_zone=restaurant.release_time_zone,
                release_datetime=release_dt,
                scan_start_datetime=scan_start,
                target_date=target_date,
                restaurants=[restaurant],
            )

    windows = sorted(window_map.values(), key=lambda w: w.scan_start_datetime)
    return windows


class Scheduler:
    def __init__(
        self,
        restaurants: list[RestaurantConfig],
        scan_start_seconds_before: int = 45,
        on_window_start: Callable[[ReleaseWindow], Awaitable[None]] | None = None,
    ):
        self._restaurants = restaurants
        self._scan_start_before = scan_start_seconds_before
        self._on_window_start = on_window_start
        self._running = False
        self._fired_windows: set[str] = set()

    async def run(self) -> None:
        """Main scheduler loop. Calculates next window, sleeps until it starts, fires callback."""
        self._running = True
        log.info("scheduler_started")

        while self._running:
            windows = calculate_release_windows(
                self._restaurants,
                self._scan_start_before,
            )

            # Skip windows we already fired
            windows = [w for w in windows if w.id not in self._fired_windows]

            if not windows:
                log.info("no_upcoming_windows", rechecking_in="1 hour")
                await asyncio.sleep(3600)
                # Reset fired windows after an hour so next day's windows work
                self._fired_windows.clear()
                continue

            window = windows[0]
            now = datetime.now(window.scan_start_datetime.tzinfo)
            sleep_s = (window.scan_start_datetime - now).total_seconds()

            if sleep_s > 0:
                log.info(
                    "window_scheduled",
                    window_id=window.id,
                    release_time=window.release_time,
                    target_date=window.target_date,
                    restaurants=[r.name for r in window.restaurants],
                    scan_starts_in_s=round(sleep_s),
                )
                await asyncio.sleep(sleep_s)

            if not self._running:
                break

            self._fired_windows.add(window.id)

            log.info(
                "window_starting",
                window_id=window.id,
                target_date=window.target_date,
                restaurants=[r.name for r in window.restaurants],
            )

            if self._on_window_start:
                await self._on_window_start(window)

    def stop(self) -> None:
        self._running = False
        log.info("scheduler_stopped")
