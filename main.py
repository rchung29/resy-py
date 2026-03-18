"""Unified resy-sniper service.

Single async process: scheduler + calendar monitor → scanner → booker.

Usage:
    python -m main
"""

import asyncio
import signal

from booking.account_manager import AccountManager
from booking.auth_cache import AuthCache
from booking.booker import Booker
from booking.checkout_pool import CheckoutPool
from booking.notifications import DiscordNotifier
from monitor.calendar_monitor import CalendarMonitor
from monitor.proxy_rotation import ProxyRotator
from monitor.scanner import Scanner
from monitor.scheduler import Scheduler
from shared.config import Settings, load_restaurants
from shared.logger import get_logger, setup_logger
from shared.models import DiscoveredSlotsMessage

log = get_logger("main")


async def main() -> None:
    settings = Settings()
    setup_logger("resy-sniper", settings.log_level)

    restaurants = load_restaurants()

    scan_proxies = settings.scan_proxies
    book_proxies = settings.book_proxies
    users = settings.users

    log.info(
        "config_loaded",
        restaurants=len(restaurants),
        scan_proxies=len(scan_proxies),
        book_proxies=len(book_proxies),
        users=len(users),
        dry_run=settings.dry_run,
    )

    # --- Auth cache ---
    auth_cache = AuthCache(
        api_key=settings.resy_api_key,
        proxy_url=book_proxies[0].url if book_proxies else None,
    )
    auth_cache.register_users(users)
    await auth_cache.warm_all()

    # --- Proxy rotation ---
    scan_rotator = ProxyRotator(scan_proxies) if settings.use_proxies and scan_proxies else None
    checkout_pool = CheckoutPool(book_proxies)

    # --- Account manager ---
    account_mgr = AccountManager(
        users=users,
        auth_cache=auth_cache,
        api_key=settings.resy_api_key,
        proxy_url=book_proxies[0].url if book_proxies else None,
    )
    if settings.prefetch_reservations:
        await account_mgr.prefetch_reservations()
    else:
        log.info("prefetch_disabled")

    # --- Booker + notifier ---
    notifier = DiscordNotifier(settings.discord_webhook_url or None)
    booker = Booker(
        api_key=settings.resy_api_key,
        checkout_pool=checkout_pool,
        dry_run=settings.dry_run,
    )

    # --- Booking state ---
    date_state: dict[str, dict] = {}
    booked_keys: set[str] = set()

    def get_date_state(target_date: str) -> dict:
        if target_date not in date_state:
            date_state[target_date] = {
                "auth_failed": set(),
            }
        return date_state[target_date]

    async def on_slots_discovered(
        msg: DiscoveredSlotsMessage,
        source: str = "sniper",
    ) -> None:
        """Called by scanner or calendar monitor when slots are found.

        source: "sniper" (release window) or "monitor" (passive poll)

        Sniper: on auth_failed, drop the user for the rest of the window.
        Monitor: on auth_failed, invalidate cached token and move on.
        """
        state = get_date_state(msg.target_date)
        exclude = state["auth_failed"]

        while True:
            per_venue_exclude = exclude | {
                key.split(":")[0]
                for key in booked_keys
                if key.endswith(f":{msg.venue_id}:{msg.target_date}")
            }

            user = account_mgr.get_available_user(msg.target_date, per_venue_exclude)
            if not user:
                log.info(
                    "no_available_users",
                    venue=msg.restaurant_name,
                    date=msg.target_date,
                    source=source,
                )
                break

            # Get credentials from auth cache
            creds = await account_mgr.get_user_credentials(user.id)
            if not creds:
                log.error("no_credentials", user=user.id, source=source)
                state["auth_failed"].add(user.id)
                continue

            result = await booker.process_slots(
                user=user,
                auth_token=creds.auth_token,
                payment_method_id=creds.payment_method_id,
                venue_id=msg.venue_id,
                target_date=msg.target_date,
                party_size=msg.party_size,
                slots=msg.slots,
            )

            if result.success:
                booked_keys.add(f"{user.id}:{msg.venue_id}:{msg.target_date}")
                log.info(
                    "BOOKING_SUCCESS",
                    user=user.id,
                    restaurant=msg.restaurant_name,
                    reservation_id=result.reservation_id,
                    source=source,
                )
                await notifier.notify_booking_success(
                    restaurant_name=msg.restaurant_name,
                    target_date=msg.target_date,
                    slot_time=msg.slots[0].time if msg.slots else "unknown",
                    user_id=user.id,
                    reservation_id=result.reservation_id or 0,
                )
                break

            if result.status == "auth_failed":
                if source == "sniper":
                    # Drop user for the rest of this window — no reauth mid-window
                    state["auth_failed"].add(user.id)
                    log.error(
                        "user_auth_failed",
                        user=user.id,
                        date=msg.target_date,
                        source=source,
                    )
                    continue
                else:
                    # Monitor: invalidate token, move on (next poll gets fresh creds)
                    await auth_cache.invalidate(user.id)
                    log.warning(
                        "user_auth_failed_invalidated",
                        user=user.id,
                        date=msg.target_date,
                        source=source,
                    )
                    break

            log.info(
                "booking_failed",
                user=user.id,
                restaurant=msg.restaurant_name,
                status=result.status,
                source=source,
            )
            await notifier.notify_booking_failed(
                restaurant_name=msg.restaurant_name,
                target_date=msg.target_date,
                error_message=result.error_message or "All slots failed",
                status=result.status,
                http_status=result.http_status,
                user_id=user.id,
                slots_tried=len(msg.slots),
            )
            break

    # --- Scanner (sniper source) ---
    async def sniper_on_slots(msg: DiscoveredSlotsMessage) -> None:
        await on_slots_discovered(msg, source="sniper")

    scanner = Scanner(
        api_key=settings.resy_api_key,
        scan_interval_ms=settings.scan_interval_ms,
        scan_timeout_seconds=settings.scan_timeout_seconds,
        proxy_rotator=scan_rotator,
        use_proxies=settings.use_proxies,
        on_slots_discovered=sniper_on_slots,
    )

    # --- Scheduler ---
    async def on_window_start(window):
        log.info(
            "window_triggered",
            restaurants={r.name: window.target_date_for(r.venue_id) for r in window.restaurants},
        )
        await scanner.start_scan(window)

    scheduler = Scheduler(
        restaurants=[r for r in restaurants if r.enabled],
        scan_start_seconds_before=settings.scan_start_seconds_before,
        on_window_start=on_window_start,
    )

    # --- Calendar monitor (passive_monitor: true restaurants) ---
    monitor_restaurants = [r for r in restaurants if r.enabled and r.passive_monitor]
    calendar_monitor = None

    if monitor_restaurants:
        async def monitor_on_slots(msg: DiscoveredSlotsMessage) -> None:
            await on_slots_discovered(msg, source="monitor")

        calendar_monitor = CalendarMonitor(
            restaurants=monitor_restaurants,
            api_key=settings.resy_api_key,
            poll_interval_s=settings.passive_monitor_interval_s,
            calendar_days=settings.passive_monitor_calendar_days,
            proxy_rotator=scan_rotator,
            use_proxies=settings.use_proxies,
            on_slots_discovered=monitor_on_slots,
        )
        log.info(
            "calendar_monitor_enabled",
            restaurants=[r.name for r in monitor_restaurants],
        )

    # --- Graceful shutdown ---
    loop = asyncio.get_event_loop()
    for sig_ in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig_,
            lambda: asyncio.create_task(shutdown(scanner, scheduler, calendar_monitor)),
        )

    # --- Run both concurrently ---
    tasks = [asyncio.create_task(scheduler.run())]
    if calendar_monitor:
        tasks.append(asyncio.create_task(calendar_monitor.run()))

    log.info("resy_sniper_started", scheduler=True, calendar_monitor=calendar_monitor is not None)
    await asyncio.gather(*tasks)


async def shutdown(scanner: Scanner, scheduler: Scheduler, monitor=None) -> None:
    log.info("shutting_down")
    scheduler.stop()
    scanner.stop_all()
    if monitor:
        monitor.stop()
    await asyncio.sleep(0.5)
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
