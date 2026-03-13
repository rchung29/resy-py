"""Shared slot parsing utilities."""

from shared.models import SlotData


def parse_find_slots_response(response: dict) -> tuple[str | None, list[SlotData]]:
    """Parse a /4/find response into (venue_name, slot_datas).

    Returns (None, []) if no venue or slots found.
    """
    venues = response.get("results", {}).get("venues", [])
    if not venues:
        return None, []

    venue = venues[0]
    raw_slots = venue.get("slots", [])
    if not raw_slots:
        return None, []

    venue_name = venue.get("venue", {}).get("name")

    slot_datas = [
        SlotData(
            config_id=s.get("config", {}).get("token", ""),
            time=s.get("date", {}).get("start", ""),
            type=s.get("config", {}).get("type"),
        )
        for s in raw_slots
        if s.get("config", {}).get("token") and s.get("date", {}).get("start")
    ]

    return venue_name, slot_datas
