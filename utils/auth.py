"""Authentication utilities — login, token refresh, account validation.

Ported from ResyBot's login and acc_preloader flows.

Usage:
    from utils.auth import login, check_account_usable, refresh_auth_token

    # Login with email/password
    creds = await login("user@email.com", "password123", proxy_url="...")
    # creds = {"auth_token": "...", "payment_method_id": 12345}

    # Check if account has no existing reservations
    usable = await check_account_usable(auth_token, proxy_url="...")

    # Refresh an existing user's auth token
    new_creds = await refresh_auth_token("user@email.com", "password123")
"""

from __future__ import annotations

import random

import aiohttp

from sdk.client import BROWSER_USER_AGENTS, RESY_API_BASE
from sdk.errors import ResyAPIError
from shared.logger import get_logger

log = get_logger("auth")

RESY_API_KEY = "VbWk7s3L4KiK5fzlO7JD3Q5EYolJI7n5"


async def login(
    email: str,
    password: str,
    proxy_url: str | None = None,
    api_key: str = RESY_API_KEY,
) -> dict:
    """Login to Resy with email/password.

    Returns dict with: auth_token, payment_method_id, first_name, last_name.
    Raises ResyAPIError or RuntimeError on failure.
    """
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "en-US,en;q=0.9",
        "Authorization": f'ResyAPI api_key="{api_key}"',
        "Cache-Control": "no-cache",
        "Content-Type": "application/x-www-form-urlencoded",
        "Dnt": "1",
        "Origin": "https://resy.com",
        "Referer": "https://resy.com/",
        "User-Agent": random.choice(BROWSER_USER_AGENTS),
        "X-Origin": "https://resy.com",
    }

    payload = {"email": email, "password": password}

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{RESY_API_BASE}/3/auth/password",
            headers=headers,
            data=payload,
            proxy=proxy_url,
            ssl=False,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            body = await resp.text()
            if resp.status >= 300:
                raise ResyAPIError(
                    f"Login failed: {resp.status}",
                    resp.status,
                    raw_body=body,
                )
            import json
            data = json.loads(body)

    auth_token = data.get("token")
    if not auth_token:
        raise RuntimeError("Login response missing token")

    result = {
        "auth_token": auth_token,
        "payment_method_id": data.get("payment_method_id", 0),
        "first_name": data.get("first_name", ""),
        "last_name": data.get("last_name", ""),
    }

    log.info("login_success", email=email)
    return result


async def check_account_usable(
    auth_token: str,
    proxy_url: str | None = None,
    api_key: str = RESY_API_KEY,
) -> bool:
    """Check if an account has no existing upcoming reservations.

    Returns True if the account is usable (0 upcoming reservations).
    """
    headers = {
        "Accept": "*/*",
        "Accept-Encoding": "br;q=1.0, gzip;q=0.9, deflate;q=0.8",
        "Accept-Language": "en-US;q=1.0, fr-US;q=0.9",
        "Authorization": f'ResyAPI api_key="{api_key}"',
        "Connection": "keep-alive",
        "Host": "api.resy.com",
        "User-Agent": random.choice(BROWSER_USER_AGENTS),
        "X-Resy-Auth-Token": auth_token,
        "X-Resy-Universal-Auth": auth_token,
        "Cache-Control": "no-cache",
    }

    params = {
        "limit": "1",
        "offset": "1",
        "type": "upcoming",
        "book_on_behalf_of": "false",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{RESY_API_BASE}/3/user/reservations",
                headers=headers,
                params=params,
                proxy=proxy_url,
                ssl=False,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return False
                import json
                data = json.loads(await resp.text())

        reservations = data.get("reservations", [])
        return len(reservations) == 0

    except Exception as e:
        log.warning("check_usable_error", error=str(e))
        return False


async def refresh_auth_token(
    email: str,
    password: str,
    proxy_url: str | None = None,
    api_key: str = RESY_API_KEY,
) -> dict:
    """Convenience wrapper: login and return fresh credentials.

    Same as login() but logs the intent as a refresh.
    """
    log.info("refreshing_auth_token", email=email)
    return await login(email, password, proxy_url=proxy_url, api_key=api_key)
