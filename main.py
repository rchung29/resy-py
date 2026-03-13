"""Unified resy-sniper service.

Single async process: scheduler → scanner → booker. No WebSocket layer.

Usage:
    python -m main
"""

import asyncio
import signal

from booking.account_manager import AccountManager
from booking.booker import Booker
from booking.checkout_pool import CheckoutPool
from booking.notifications import DiscordNotifier
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

    # --- Proxy rotation ---
    scan_rotator = ProxyRotator(scan_proxies) if settings.use_proxies and scan_proxies else None
    checkout_pool = CheckoutPool(book_proxies)

    # --- Account manager ---
    account_mgr = AccountManager(
        users=users,
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

    async def on_slots_discovered(msg: DiscoveredSlotsMessage) -> None:
        """Called directly by scanner when slots are found — no WebSocket."""
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
                )
                break

            result = await booker.process_slots(
                user=user,
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
                state["auth_failed"].add(user.id)
                log.error("user_auth_failed", user=user.id, date=msg.target_date)
                continue

            log.info(
                "booking_failed",
                user=user.id,
                restaurant=msg.restaurant_name,
                status=result.status,
            )
            await notifier.notify_booking_failed(
                restaurant_name=msg.restaurant_name,
                target_date=msg.target_date,
                error_message=result.error_message or "All slots failed",
            )
            break

    # --- Scanner (calls on_slots_discovered directly) ---
    scanner = Scanner(
        api_key=settings.resy_api_key,
        scan_interval_ms=settings.scan_interval_ms,
        scan_timeout_seconds=settings.scan_timeout_seconds,
        proxy_rotator=scan_rotator,
        use_proxies=settings.use_proxies,
        on_slots_discovered=on_slots_discovered,
    )

    # --- Scheduler ---
    async def on_window_start(window):
        log.info(
            "window_triggered",
            restaurants={r.name: window.target_date_for(r.venue_id) for r in window.restaurants},
        )
        await scanner.start_scan(window)

    scheduler = Scheduler(
        restaurants=restaurants,
        scan_start_seconds_before=settings.scan_start_seconds_before,
        on_window_start=on_window_start,
    )

    # --- Graceful shutdown ---
    loop = asyncio.get_event_loop()
    for sig_ in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig_, lambda: asyncio.create_task(shutdown(scanner, scheduler)))

    log.info("resy_sniper_started")
    await scheduler.run()


async def shutdown(scanner: Scanner, scheduler: Scheduler) -> None:
    log.info("shutting_down")
    scheduler.stop()
    scanner.stop_all()
    await asyncio.sleep(0.5)
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
