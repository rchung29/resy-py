import asyncio
import signal

from monitor.proxy_rotation import ProxyRotator
from monitor.scanner import Scanner
from monitor.scheduler import Scheduler
from monitor.ws_server import SlotBroadcastServer
from shared.config import MonitorSettings, load_restaurants
from shared.logger import get_logger, setup_logger

log = get_logger("main")


async def main() -> None:
    settings = MonitorSettings()
    setup_logger("monitor", settings.log_level)

    restaurants = load_restaurants()

    log.info(
        "config_loaded",
        restaurants=len(restaurants),
        proxies=len(settings.monitor_proxy_urls),
        use_proxies=settings.use_proxies,
    )

    # Proxy rotation
    proxy_rotator = ProxyRotator(settings.monitor_proxy_urls) if settings.use_proxies else None

    # WebSocket server
    ws_server = SlotBroadcastServer(host=settings.ws_host, port=settings.ws_port)

    # Scanner
    scanner = Scanner(
        api_key=settings.resy_api_key,
        scan_interval_ms=settings.scan_interval_ms,
        scan_timeout_seconds=settings.scan_timeout_seconds,
        proxy_rotator=proxy_rotator,
        use_proxies=settings.use_proxies,
        on_slots_discovered=ws_server.broadcast,
    )

    # Scheduler
    async def on_window_start(window):
        log.info(
            "window_triggered",
            target_date=window.target_date,
            restaurants=[r.name for r in window.restaurants],
        )
        await scanner.start_scan(window)

    scheduler = Scheduler(
        restaurants=restaurants,
        scan_start_seconds_before=settings.scan_start_seconds_before,
        on_window_start=on_window_start,
    )

    # Graceful shutdown
    loop = asyncio.get_event_loop()
    for sig_ in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig_, lambda: asyncio.create_task(shutdown(scanner, scheduler)))

    log.info("monitor_started")
    await asyncio.gather(ws_server.start(), scheduler.run())


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
