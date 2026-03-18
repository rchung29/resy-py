"""Test auth flow — logs in all configured users and prints credentials.

Usage:
    python test_auth.py
"""

import asyncio

from booking.auth_cache import AuthCache
from shared.config import Settings
from shared.logger import get_logger, setup_logger

log = get_logger("test_auth")


async def main() -> None:
    settings = Settings()
    setup_logger("test-auth", settings.log_level)

    users = settings.users
    if not users:
        print("No users configured in BOOKING_USERS")
        return

    print(f"Testing auth for {len(users)} user(s)...\n")

    auth_cache = AuthCache(api_key=settings.resy_api_key)
    auth_cache.register_users(users)

    for user in users:
        print(f"--- {user.id} ({user.email}) ---")
        creds = await auth_cache.get_credentials(user.id)
        if creds:
            print(f"  auth_token:        {creds.auth_token[:20]}...")
            print(f"  payment_method_id: {creds.payment_method_id}")
            print(f"  status:            OK")
        else:
            print(f"  status:            FAILED")
        print()

    # Test invalidation + re-login
    if users:
        first = users[0]
        print(f"Testing invalidation + re-login for {first.id}...")
        await auth_cache.invalidate(first.id)
        creds = await auth_cache.get_credentials(first.id)
        if creds:
            print(f"  Re-login:          OK (token: {creds.auth_token[:20]}...)")
        else:
            print(f"  Re-login:          FAILED")

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
