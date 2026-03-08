from pydantic import BaseModel


# --- Restaurant config (loaded from YAML) ---

class DayConfig(BaseModel):
    day: int       # Python weekday: 0=Mon..6=Sun
    start: str     # "HH:MM"
    end: str       # "HH:MM"


class RestaurantConfig(BaseModel):
    venue_id: str
    name: str
    days_in_advance: int
    release_time: str              # "HH:MM"
    release_time_zone: str = "America/New_York"
    party_size: int = 2
    day_configs: list[DayConfig] = []
    enabled: bool = True


class ProxyConfig(BaseModel):
    url: str


# --- User config (loaded from env JSON) ---

class UserConfig(BaseModel):
    id: str
    resy_auth_token: str
    resy_payment_method_id: int


# --- WebSocket message ---

class SlotData(BaseModel):
    config_id: str
    time: str
    type: str | None = None


class DiscoveredSlotsMessage(BaseModel):
    venue_id: str
    restaurant_name: str
    target_date: str       # YYYY-MM-DD
    party_size: int
    slots: list[SlotData]


# --- Internal booking types ---

class ExistingReservation(BaseModel):
    date: str
    venue_id: int
    venue_name: str
    time_slot: str
