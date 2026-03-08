import asyncio
from typing import Awaitable, Callable

import websockets

from shared.logger import get_logger
from shared.models import DiscoveredSlotsMessage

log = get_logger("ws_client")


class SlotReceiver:
    def __init__(
        self,
        monitor_url: str,
        on_slots_received: Callable[[DiscoveredSlotsMessage], Awaitable[None]],
    ):
        self._url = monitor_url
        self._on_slots_received = on_slots_received

    async def run(self) -> None:
        """Connect to monitor WS server with auto-reconnect."""
        while True:
            try:
                async for ws in websockets.connect(self._url):
                    log.info("connected_to_monitor", url=self._url)
                    try:
                        async for raw in ws:
                            await self._handle_message(raw)
                    except websockets.ConnectionClosed:
                        log.warning("monitor_connection_lost")
                        continue
            except Exception as e:
                log.error("ws_connect_error", error=str(e))
                await asyncio.sleep(5)

    async def _handle_message(self, raw: str) -> None:
        try:
            msg = DiscoveredSlotsMessage.model_validate_json(raw)
            log.info(
                "slots_received",
                restaurant=msg.restaurant_name,
                date=msg.target_date,
                slots=len(msg.slots),
            )
            asyncio.create_task(self._on_slots_received(msg))
        except Exception as e:
            log.error("message_parse_error", error=str(e), raw=raw[:200])
