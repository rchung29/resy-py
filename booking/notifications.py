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
        self,
        restaurant_name: str,
        target_date: str,
        error_message: str,
        status: str | None = None,
        http_status: int | None = None,
        user_id: str | None = None,
        slots_tried: int | None = None,
    ) -> bool:
        status_labels = {
            "waf_blocked": "WAF Blocked (Cloudflare)",
            "rate_limited": "Rate Limited (429)",
            "auth_failed": "Auth Token Expired/Invalid",
            "sold_out": "All Slots Sold Out",
            "no_book_token": "No Book Token Returned",
            "server_error": "Resy Server Error",
            "unknown": "Unknown Error",
        }

        fields = [
            {"name": "Date", "value": target_date, "inline": True},
        ]
        if user_id:
            fields.append({"name": "Account", "value": user_id, "inline": True})
        if status:
            label = status_labels.get(status, status)
            fields.append({"name": "Status", "value": label, "inline": True})
        if http_status:
            fields.append({"name": "HTTP Code", "value": str(http_status), "inline": True})
        if slots_tried:
            fields.append({"name": "Slots Tried", "value": str(slots_tried), "inline": True})
        fields.append({"name": "Detail", "value": error_message})

        return await self._send_embed({
            "title": "Booking Failed",
            "description": f"Could not book at **{restaurant_name}**",
            "color": 0xE74C3C,
            "fields": fields,
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
