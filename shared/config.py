import json

import yaml
from pydantic_settings import BaseSettings

from shared.models import ProxyConfig, RestaurantConfig, UserConfig

DEFAULT_RESTAURANTS_PATH = "config/restaurants.yaml"

# Public key, safe to hardcode as default
DEFAULT_RESY_API_KEY = "VbWk7s3L4KiK5fzlO7JD3Q5EYolJI7n5"


def _parse_comma_proxies(raw: str) -> list[ProxyConfig]:
    if not raw.strip():
        return []
    return [ProxyConfig(url=u.strip()) for u in raw.split(",") if u.strip()]


def _parse_json_users(raw: str) -> list[UserConfig]:
    parsed = json.loads(raw)
    return [UserConfig.model_validate(u) for u in parsed]


class MonitorSettings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    resy_api_key: str = DEFAULT_RESY_API_KEY
    monitor_proxy_urls: str = ""
    scan_start_seconds_before: int = 15
    scan_interval_ms: int = 3500
    scan_timeout_seconds: int = 120
    use_proxies: bool = False
    ws_host: str = "0.0.0.0"
    ws_port: int = 8765
    log_level: str = "INFO"

    @property
    def proxies(self) -> list[ProxyConfig]:
        return _parse_comma_proxies(self.monitor_proxy_urls)


class BookingSettings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    resy_api_key: str = DEFAULT_RESY_API_KEY
    booking_proxy_urls: str = ""
    booking_users: str = "[]"
    discord_webhook_url: str = ""
    dry_run: bool = False
    monitor_ws_url: str = "ws://localhost:8765"
    log_level: str = "INFO"

    @property
    def proxies(self) -> list[ProxyConfig]:
        return _parse_comma_proxies(self.booking_proxy_urls)

    @property
    def users(self) -> list[UserConfig]:
        return _parse_json_users(self.booking_users)


def load_restaurants(path: str | None = None) -> list[RestaurantConfig]:
    """Load restaurant configs from YAML file."""
    config_path = path or DEFAULT_RESTAURANTS_PATH
    with open(config_path) as f:
        data = yaml.safe_load(f.read())
    restaurants = data if isinstance(data, list) else data.get("restaurants", [])
    return [RestaurantConfig.model_validate(r) for r in restaurants]
