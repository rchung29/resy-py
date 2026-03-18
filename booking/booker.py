from dataclasses import dataclass

from booking.checkout_pool import CheckoutPool
from sdk.client import ResyClient
from sdk.errors import ResyAPIError
from shared.logger import get_logger
from shared.models import SlotData, UserConfig

log = get_logger("booker")

MAX_RETRIES_PER_SLOT = 2


@dataclass
class BookingResult:
    success: bool
    status: str  # "success", "waf_blocked", "sold_out", "rate_limited", "auth_failed", "server_error", "no_book_token", "unknown"
    reservation_id: int | None = None
    error_message: str | None = None
    http_status: int | None = None


class Booker:
    def __init__(
        self,
        api_key: str,
        checkout_pool: CheckoutPool,
        dry_run: bool = False,
    ):
        self._api_key = api_key
        self._checkout_pool = checkout_pool
        self._dry_run = dry_run
        self._claimed_slots: set[str] = set()

    def _slot_key(self, venue_id: str, target_date: str, slot_time: str) -> str:
        return f"{venue_id}:{target_date}:{slot_time}"

    def try_claim_slot(self, venue_id: str, target_date: str, slot_time: str) -> bool:
        key = self._slot_key(venue_id, target_date, slot_time)
        if key in self._claimed_slots:
            return False
        self._claimed_slots.add(key)
        return True

    def release_slot(self, venue_id: str, target_date: str, slot_time: str) -> None:
        self._claimed_slots.discard(self._slot_key(venue_id, target_date, slot_time))

    async def process_slots(
        self,
        user: UserConfig,
        auth_token: str,
        payment_method_id: int,
        venue_id: str,
        target_date: str,
        party_size: int,
        slots: list[SlotData],
    ) -> BookingResult:
        """Try slots sequentially until one succeeds or all fail."""
        slot_index = 0
        retry_count = 0

        log.info(
            "processing_slots",
            user=user.id,
            venue_id=venue_id,
            slots_to_try=len(slots),
        )

        while slot_index < len(slots):
            slot = slots[slot_index]

            # Claim slot
            if not self.try_claim_slot(venue_id, target_date, slot.time):
                log.debug("slot_already_claimed", time=slot.time)
                slot_index += 1
                continue

            result = await self.attempt_booking(
                user, auth_token, payment_method_id,
                venue_id, target_date, party_size, slot,
            )

            if result.success:
                return result

            match result.status:
                case "waf_blocked":
                    retry_count += 1
                    if retry_count >= MAX_RETRIES_PER_SLOT:
                        log.warning(
                            "max_waf_retries",
                            user=user.id,
                            time=slot.time,
                            retries=retry_count,
                        )
                        self.release_slot(venue_id, target_date, slot.time)
                        slot_index += 1
                        retry_count = 0
                    else:
                        log.info("waf_retry", user=user.id, time=slot.time, retry=retry_count)

                case "auth_failed":
                    self.release_slot(venue_id, target_date, slot.time)
                    return result

                case _:
                    # sold_out, rate_limited, server_error, unknown — move to next slot
                    self.release_slot(venue_id, target_date, slot.time)
                    slot_index += 1
                    retry_count = 0

        return BookingResult(
            success=False,
            status="unknown",
            error_message="All slots failed",
        )

    async def attempt_booking(
        self,
        user: UserConfig,
        auth_token: str,
        payment_method_id: int,
        venue_id: str,
        target_date: str,
        party_size: int,
        slot: SlotData,
    ) -> BookingResult:
        """Execute details -> book for a single slot."""
        proxy_url = self._checkout_pool.get_next()

        client = ResyClient(
            api_key=self._api_key,
            auth_token=auth_token,
            proxy_url=proxy_url,
        )

        try:
            log.info(
                "attempting_booking",
                user=user.id,
                venue_id=venue_id,
                time=slot.time,
                proxy=proxy_url is not None,
            )

            # Step 1: Get details + book token
            details = await client.get_details(
                venue_id=int(venue_id),
                day=target_date,
                party_size=party_size,
                config_id=slot.config_id,
            )

            book_token = details.get("book_token", {}).get("value")
            if not book_token:
                return BookingResult(
                    success=False,
                    status="no_book_token",
                    error_message="No book token received",
                )

            # Step 2: Dry run check
            if self._dry_run:
                log.info("dry_run_would_book", user=user.id, time=slot.time)
                return BookingResult(
                    success=True,
                    status="success",
                    reservation_id=0,
                )

            # Step 3: Book
            book_result = await client.book_reservation(
                book_token=book_token,
                payment_method_id=payment_method_id,
            )

            reservation_id = book_result.get("reservation_id")
            log.info(
                "booking_success",
                user=user.id,
                venue_id=venue_id,
                time=slot.time,
                reservation_id=reservation_id,
            )

            return BookingResult(
                success=True,
                status="success",
                reservation_id=reservation_id,
            )

        except ResyAPIError as e:
            result = self._classify_error(e)

            if result.status == "waf_blocked" and proxy_url:
                self._checkout_pool.mark_bad(proxy_url)

            log.warning(
                "booking_error",
                user=user.id,
                time=slot.time,
                status=result.status,
                http_status=e.status,
                raw_body_len=len(e.raw_body) if e.raw_body else 0,
            )
            return result

        except Exception as e:
            log.error("booking_unexpected_error", user=user.id, error=str(e))
            return BookingResult(
                success=False,
                status="unknown",
                error_message=str(e),
            )
        finally:
            await client.close()

    def _classify_error(self, error: ResyAPIError) -> BookingResult:
        """Classify API error into booking status."""
        raw = error.raw_body or ""
        hs = error.status

        # WAF block: 500 with empty/whitespace/{} body
        if hs == 500:
            if not raw.strip() or raw.strip() == "{}":
                return BookingResult(
                    success=False,
                    status="waf_blocked",
                    error_message="WAF blocked (500 empty body)",
                    http_status=hs,
                )
            return BookingResult(
                success=False,
                status="server_error",
                error_message=str(error),
                http_status=hs,
            )

        # 412 = sold out
        if hs == 412:
            return BookingResult(
                success=False,
                status="sold_out",
                error_message="Slot no longer available",
                http_status=hs,
            )

        # 419 = auth token expired/invalid
        if hs == 419:
            return BookingResult(
                success=False,
                status="auth_failed",
                error_message="Auth token expired or invalid (419)",
                http_status=hs,
            )

        # 429 = rate limited
        if hs == 429:
            return BookingResult(
                success=False,
                status="rate_limited",
                error_message="Rate limited",
                http_status=hs,
            )

        # 401/403 = auth failed
        if hs in (401, 403):
            return BookingResult(
                success=False,
                status="auth_failed",
                error_message=str(error),
                http_status=hs,
            )

        return BookingResult(
            success=False,
            status="unknown",
            error_message=f"HTTP {hs}: {raw[:200]}" if raw else str(error),
            http_status=hs,
        )

    def reset(self) -> None:
        """Reset state for a new window."""
        self._claimed_slots.clear()
