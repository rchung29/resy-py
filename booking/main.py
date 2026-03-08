import asyncio
import signal

from booking.account_manager import AccountManager
from booking.booker import Booker
from booking.checkout_pool import CheckoutPool
from booking.notifications import DiscordNotifier
from booking.ws_client import SlotReceiver
from shared.config import BookingSettings
from shared.logger import get_logger, setup_logger
from shared.models import DiscoveredSlotsMessage

log = get_logger("main")


async def main() -> None:
    settings = BookingSettings()
    setup_logger("booking", settings.log_level)

    log.info(
        "config_loaded",
        users=len(settings.booking_users),
        proxies=len(settings.booking_proxy_urls),
        dry_run=settings.dry_run,
    )

    # Account manager — prefetch existing reservations
    account_mgr = AccountManager(
        users=settings.booking_users,
        api_key=settings.resy_api_key,
        proxy_url=settings.booking_proxy_urls[0].url if settings.booking_proxy_urls else None,
    )
    await account_mgr.prefetch_reservations()

    # Services
    notifier = DiscordNotifier(settings.discord_webhook_url or None)
    checkout_pool = CheckoutPool(settings.booking_proxy_urls)
    booker = Booker(
        api_key=settings.resy_api_key,
        checkout_pool=checkout_pool,
        dry_run=settings.dry_run,
    )

    # State keyed by target_date
    date_state: dict[str, dict] = {}
    booked_keys: set[str] = set()

    def get_date_state(target_date: str) -> dict:
        if target_date not in date_state:
            date_state[target_date] = {
                "rate_limited": set(),
                "auth_failed": set(),
            }
        return date_state[target_date]

    async def on_slots_received(msg: DiscoveredSlotsMessage) -> None:
        state = get_date_state(msg.target_date)

        exclude = state["rate_limited"] | state["auth_failed"]

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

            if result.status == "rate_limited":
                state["rate_limited"].add(user.id)
                log.warning("user_rate_limited", user=user.id, date=msg.target_date)
                continue

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

    # WebSocket client
    receiver = SlotReceiver(
        monitor_url=settings.monitor_ws_url,
        on_slots_received=on_slots_received,
    )

    # Graceful shutdown
    loop = asyncio.get_event_loop()
    for sig_ in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig_, lambda: asyncio.create_task(_shutdown()))

    log.info("booking_service_started")
    await receiver.run()


async def _shutdown() -> None:
    log.info("shutting_down")
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
