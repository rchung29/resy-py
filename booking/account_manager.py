import asyncio

from sdk.client import ResyClient
from shared.logger import get_logger
from shared.models import ExistingReservation, UserConfig

log = get_logger("account_manager")

FETCH_CONCURRENCY = 5
PREFETCH_TIMEOUT_S = 30
USER_FETCH_TIMEOUT_S = 10


class AccountManager:
    def __init__(
        self,
        users: list[UserConfig],
        api_key: str,
        proxy_url: str | None = None,
    ):
        self._users = users
        self._api_key = api_key
        self._proxy_url = proxy_url
        self._reservations: dict[str, list[ExistingReservation]] = {}

    @property
    def users(self) -> list[UserConfig]:
        return self._users

    async def prefetch_reservations(self) -> None:
        """Fetch existing reservations for all users."""
        log.info("prefetch_starting", user_count=len(self._users))

        try:
            await asyncio.wait_for(
                self._do_prefetch(),
                timeout=PREFETCH_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            log.warning("prefetch_timeout")

    async def _do_prefetch(self) -> None:
        successful = 0
        failed = 0

        # Process in batches
        for i in range(0, len(self._users), FETCH_CONCURRENCY):
            batch = self._users[i : i + FETCH_CONCURRENCY]
            results = await asyncio.gather(
                *[self._fetch_user(u) for u in batch],
                return_exceptions=True,
            )
            for user, result in zip(batch, results):
                if isinstance(result, Exception):
                    log.warning(
                        "prefetch_user_failed",
                        user=user.id,
                        error=str(result),
                    )
                    self._reservations[user.id] = []
                    failed += 1
                else:
                    self._reservations[user.id] = result
                    successful += 1

        log.info(
            "prefetch_complete",
            successful=successful,
            failed=failed,
        )

    async def _fetch_user(self, user: UserConfig) -> list[ExistingReservation]:
        """Fetch reservations for a single user with timeout."""
        client = ResyClient(
            api_key=self._api_key,
            auth_token=user.resy_auth_token,
            proxy_url=self._proxy_url,
        )
        try:
            response = await asyncio.wait_for(
                client.get_user_reservations("upcoming"),
                timeout=USER_FETCH_TIMEOUT_S,
            )
            reservations = [
                ExistingReservation(
                    date=r.get("day", ""),
                    venue_id=r.get("venue", {}).get("id", 0),
                    venue_name=r.get("venue", {}).get("name", "Unknown"),
                    time_slot=r.get("time_slot", ""),
                )
                for r in response.get("reservations", [])
            ]

            if reservations:
                log.info(
                    "user_has_reservations",
                    user=user.id,
                    count=len(reservations),
                    dates=[r.date for r in reservations],
                )

            return reservations
        finally:
            await client.close()

    def has_reservation_on_date(self, user_id: str, target_date: str) -> bool:
        """Check if user has an existing reservation on the given date."""
        reservations = self._reservations.get(user_id, [])
        return any(r.date == target_date for r in reservations)

    def get_available_user(
        self,
        target_date: str,
        exclude_ids: set[str],
    ) -> UserConfig | None:
        """Pick the first available user with no conflict on target_date."""
        for user in self._users:
            if user.id in exclude_ids:
                continue
            if self.has_reservation_on_date(user.id, target_date):
                continue
            return user
        return None
