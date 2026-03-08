from datetime import datetime, timezone

import aiohttp

from shared.logger import get_logger

log = get_logger("notifications")


class DiscordNotifier:
    def __init__(self, webhook_url: str | None):
        self._url = webhook_url

    async def notify_booking_success(
        self,
        restaurant_name: str,
        target_date: str,
        slot_time: str,
        user_id: str,
        reservation_id: int,
        table_type: str | None = None,
    ) -> bool:
        fields = [
            {"name": "Date", "value": target_date, "inline": True},
            {"name": "Time", "value": slot_time, "inline": True},
            {"name": "Reservation ID", "value": str(reservation_id), "inline": True},
            {"name": "Account", "value": user_id, "inline": True},
        ]
        if table_type:
            fields.append({"name": "Table Type", "value": table_type, "inline": True})

        return await self._send_embed({
            "title": "BOOKED",
            "description": f"Successfully booked at **{restaurant_name}**",
            "color": 0x2ECC71,
            "fields": fields,
            "footer": {"text": "Check your Resy app to view details."},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def notify_booking_failed(
        self, restaurant_name: str, target_date: str, error_message: str
    ) -> bool:
        return await self._send_embed({
            "title": "Booking Failed",
            "description": f"Could not book at **{restaurant_name}**",
            "color": 0xE74C3C,
            "fields": [
                {"name": "Date", "value": target_date, "inline": True},
                {"name": "Reason", "value": error_message},
            ],
            "footer": {"text": "The system will try again when a future window opens."},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def _send_embed(self, embed: dict) -> bool:
        if not self._url:
            return False

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._url,
                    json={"embeds": [embed], "username": "Resy Sniper"},
                ) as resp:
                    if resp.status >= 300:
                        body = await resp.text()
                        log.error("webhook_failed", status=resp.status, body=body[:500])
                        return False
                    return True
        except Exception as e:
            log.error("webhook_error", error=str(e))
            return False
