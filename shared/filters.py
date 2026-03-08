from datetime import datetime, time

from shared.models import DayConfig, SlotData


def parse_slot_time(slot_time: str) -> time:
    """Parse 'YYYY-MM-DD HH:MM:SS' from date.start into a time object."""
    dt = datetime.strptime(slot_time, "%Y-%m-%d %H:%M:%S")
    return dt.time()


def is_time_in_window(slot_time: str, start: str, end: str) -> bool:
    """Check if slot time falls within start-end window (inclusive)."""
    slot = parse_slot_time(slot_time)
    window_start = time.fromisoformat(start)
    window_end = time.fromisoformat(end)
    return window_start <= slot <= window_end


def filter_slots(
    slots: list[SlotData],
    day_configs: list[DayConfig],
    target_date: str,
) -> list[SlotData]:
    """Filter slots by day-of-week time window.

    Returns only slots whose time falls within the matching day_config window.
    If no day_config matches the target date's weekday, returns empty.
    """
    # No day_configs = accept all slots (no time filtering)
    if not day_configs:
        return list(slots)

    weekday = datetime.strptime(target_date, "%Y-%m-%d").weekday()

    config = None
    for dc in day_configs:
        if dc.day == weekday:
            config = dc
            break

    if config is None:
        return []

    return [s for s in slots if is_time_in_window(s.time, config.start, config.end)]
