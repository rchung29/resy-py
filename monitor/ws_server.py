import asyncio
import json

import websockets
from websockets.server import ServerConnection

from shared.logger import get_logger
from shared.models import DiscoveredSlotsMessage

log = get_logger("ws_server")


class SlotBroadcastServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8765):
        self._host = host
        self._port = port
        self._clients: set[ServerConnection] = set()

    async def start(self) -> None:
        """Start the WebSocket server."""
        async with websockets.serve(self._handler, self._host, self._port):
            log.info("ws_server_started", host=self._host, port=self._port)
            await asyncio.Future()  # Run forever

    async def broadcast(self, message: DiscoveredSlotsMessage) -> None:
        """Send a discovered slots message to all connected clients."""
        if not self._clients:
            log.warning("no_clients_connected")
            return

        data = message.model_dump_json()
        dead: list[ServerConnection] = []

        for ws in self._clients:
            try:
                await ws.send(data)
            except websockets.ConnectionClosed:
                dead.append(ws)
            except Exception as e:
                log.error("broadcast_error", error=str(e))
                dead.append(ws)

        for ws in dead:
            self._clients.discard(ws)

        log.debug(
            "broadcast_sent",
            clients=len(self._clients),
            restaurant=message.restaurant_name,
            slots=len(message.slots),
        )

    async def _handler(self, websocket: ServerConnection) -> None:
        """Handle a client connection."""
        self._clients.add(websocket)
        log.info("client_connected", total=len(self._clients))
        try:
            async for _ in websocket:
                pass  # We don't expect messages from clients
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(websocket)
            log.info("client_disconnected", total=len(self._clients))

    @property
    def client_count(self) -> int:
        return len(self._clients)
