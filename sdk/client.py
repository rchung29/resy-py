import json
import random

import aiohttp

from sdk.errors import ResyAPIError

RESY_API_BASE = "https://api.resy.com"
DEFAULT_TIMEOUT_S = 5.0

# Standard API key (web/default)
API_KEY_STANDARD = "VbWk7s3L4KiK5fzlO7JD3Q5EYolJI7n5"
# Mobile/widget API key — used for details/booking to hit separate rate limit bucket
API_KEY_MOBILE = "AIcdK2rLXG6TYwJseSbmrBAy3RP81ocd"

BROWSER_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:134.0) Gecko/20100101 Firefox/134.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0",
]

# sec-ch-ua values matched to browser UAs above
SEC_CH_UA_MAP = {
    "Chrome/143": '"Not/A)Brand";v="8", "Chromium";v="143", "Google Chrome";v="143"',
    "Chrome/144": '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
    "Edg/144": '"Not(A:Brand";v="8", "Chromium";v="144", "Microsoft Edge";v="144"',
}

MOBILE_USER_AGENTS = [
    "Resy/2.81 (com.resy.ResyApp; build:5433; iOS 17.4.1) Alamofire/5.8.0",
    "Resy/2.80 (com.resy.ResyApp; build:5420; iOS 17.3.1) Alamofire/5.8.0",
    "Resy/2.79 (com.resy.ResyApp; build:5410; iOS 17.2.1) Alamofire/5.8.0",
]

# Keep old name for backward compat
USER_AGENTS = BROWSER_USER_AGENTS


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

    def _get_headers(
        self,
        include_auth: bool = False,
        api_key: str | None = None,
        origin: str = "https://resy.com",
        ua_pool: str = "browser",
        content_type: str = "application/x-www-form-urlencoded",
    ) -> dict[str, str]:
        key = api_key or self.api_key
        ua = random.choice(MOBILE_USER_AGENTS if ua_pool == "mobile" else BROWSER_USER_AGENTS)
        referer = f"{origin}/"

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Cache-Control": "no-cache",
            "Content-Type": content_type,
            "Authorization": f'ResyAPI api_key="{key}"',
            "User-Agent": ua,
            "Origin": origin,
            "Referer": referer,
            "X-Origin": origin,
        }

        # Add browser fingerprint headers for non-mobile UAs
        if ua_pool == "browser":
            headers["Sec-Fetch-Dest"] = "empty"
            headers["Sec-Fetch-Mode"] = "cors"
            headers["Sec-Fetch-Site"] = "same-site"
            headers["Sec-Ch-Ua-Mobile"] = "?0"
            headers["Sec-Ch-Ua-Platform"] = '"macOS"'
            # Match sec-ch-ua to the chosen UA
            for ua_key, sec_val in SEC_CH_UA_MAP.items():
                if ua_key in ua:
                    headers["Sec-Ch-Ua"] = sec_val
                    break

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
        json_body: dict | None = None,
        include_auth: bool = False,
        api_key: str | None = None,
        origin: str = "https://resy.com",
        ua_pool: str = "browser",
    ) -> dict:
        content_type = "application/json" if json_body is not None else "application/x-www-form-urlencoded"
        session = await self._get_session()
        headers = self._get_headers(
            include_auth, api_key=api_key, origin=origin, ua_pool=ua_pool, content_type=content_type,
        )

        async with session.request(
            method,
            path,
            headers=headers,
            params=params,
            data=data,
            json=json_body,
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
        """POST /4/find - find available time slots (matches browser behavior)."""
        return await self._request(
            "POST",
            "/4/find",
            json_body={
                "lat": 0,
                "long": 0,
                "day": day,
                "party_size": party_size,
                "venue_id": venue_id,
            },
            include_auth=False,
        )

    async def get_details(
        self,
        venue_id: int,
        day: str,
        party_size: int,
        config_id: str,
        use_resybot_strategy: bool = False,
    ) -> dict:
        """GET /3/details - get booking details and book_token.

        Default: CAPTCHA bypass — auth token as query param, no auth header.
        ResyBot strategy: mobile API key, widgets origin, auth in headers.
        """
        if not self.auth_token:
            raise ValueError("Auth token required for get_details")

        if use_resybot_strategy:
            # ResyBot approach: mobile key, widgets origin, auth headers, mobile UA
            return await self._request(
                "GET",
                "/3/details",
                params={
                    "commit": 1,
                    "config_id": config_id,
                    "day": day,
                    "party_size": party_size,
                },
                include_auth=True,
                api_key=API_KEY_MOBILE,
                origin="https://widgets.resy.com",
                ua_pool="mobile",
            )
        else:
            # Modern approach: auth token as query param (captcha bypass)
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
        self,
        book_token: str,
        payment_method_id: int,
    ) -> dict:
        """POST /3/book - book the reservation via widgets origin."""
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
            origin="https://widgets.resy.com",
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

    async def get_calendar(
        self, venue_id: int, party_size: int, start_date: str, end_date: str
    ) -> dict:
        """GET /4/venue/calendar - get calendar availability for a venue."""
        return await self._request(
            "GET",
            "/4/venue/calendar",
            params={
                "venue_id": venue_id,
                "num_seats": party_size,
                "start_date": start_date,
                "end_date": end_date,
            },
        )

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
