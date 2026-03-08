import json
import random

import aiohttp

from sdk.errors import ResyAPIError

RESY_API_BASE = "https://api.resy.com"
DEFAULT_TIMEOUT_S = 5.0

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]


class ResyClient:
    def __init__(
        self,
        api_key: str = "VbWk7s3L4KiK5fzlO7JD3Q5EYolJI7n5",
        auth_token: str | None = None,
        proxy_url: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ):
        self.api_key = api_key
        self.auth_token = auth_token
        self.proxy_url = proxy_url
        self.timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                base_url=RESY_API_BASE,
                timeout=self.timeout,
            )
        return self._session

    def _get_headers(self, include_auth: bool = False) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f'ResyAPI api_key="{self.api_key}"',
            "User-Agent": random.choice(USER_AGENTS),
            "Origin": "https://resy.com",
            "Referer": "https://resy.com/",
        }
        if include_auth and self.auth_token:
            headers["X-Resy-Auth-Token"] = self.auth_token
            headers["X-Resy-Universal-Auth"] = self.auth_token
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        data: str | None = None,
        include_auth: bool = False,
    ) -> dict:
        session = await self._get_session()
        headers = self._get_headers(include_auth)

        async with session.request(
            method,
            path,
            headers=headers,
            params=params,
            data=data,
            proxy=self.proxy_url,
        ) as resp:
            raw_body = await resp.text()

            if resp.status < 200 or resp.status >= 300:
                # Try to extract error code from JSON
                code = None
                try:
                    body = json.loads(raw_body)
                    code = body.get("code")
                except Exception:
                    pass

                raise ResyAPIError(
                    f"{path} failed: {resp.status}",
                    resp.status,
                    code,
                    raw_body,
                )

            try:
                return json.loads(raw_body)
            except json.JSONDecodeError:
                return {}

    async def find_slots(self, venue_id: int, day: str, party_size: int) -> dict:
        """GET /4/find - find available time slots."""
        return await self._request(
            "GET",
            "/4/find",
            params={
                "venue_id": venue_id,
                "day": day,
                "party_size": party_size,
                "lat": 0,
                "long": 0,
            },
            include_auth=True,
        )

    async def get_details(
        self, venue_id: int, day: str, party_size: int, config_id: str
    ) -> dict:
        """GET /3/details - get booking details and book_token.

        Uses CAPTCHA bypass: auth token passed as query param, NOT header.
        """
        if not self.auth_token:
            raise ValueError("Auth token required for get_details")

        return await self._request(
            "GET",
            "/3/details",
            params={
                "day": day,
                "party_size": party_size,
                "venue_id": venue_id,
                "config_id": config_id,
                "x-resy-auth-token": self.auth_token,
            },
            include_auth=False,
        )

    async def book_reservation(
        self, book_token: str, payment_method_id: int
    ) -> dict:
        """POST /3/book - book the reservation."""
        body = (
            f"book_token={book_token}"
            f"&struct_payment_method={json.dumps({'id': payment_method_id})}"
            f"&source_id=resy.com-venue-details"
        )

        data = await self._request(
            "POST",
            "/3/book",
            data=body,
            include_auth=True,
        )

        # Handle dual response format
        if data.get("reservation_id") and data.get("resy_token"):
            return {
                "reservation_id": data["reservation_id"],
                "resy_token": data["resy_token"],
            }
        if data.get("specs"):
            return {
                "reservation_id": data["specs"]["reservation_id"],
                "resy_token": data["specs"]["resy_token"],
            }
        return data

    async def get_user_reservations(
        self, reservation_type: str = "upcoming"
    ) -> dict:
        """GET /3/user/reservations"""
        return await self._request(
            "GET",
            "/3/user/reservations",
            params={"type": reservation_type},
            include_auth=True,
        )

    async def cancel_reservation(self, resy_token: str) -> None:
        """POST /3/cancel"""
        await self._request(
            "POST",
            "/3/cancel",
            data=f"resy_token={resy_token}",
            include_auth=True,
        )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
