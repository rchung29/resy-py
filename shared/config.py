import json
from typing import Annotated

import yaml
from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings

from shared.models import ProxyConfig, RestaurantConfig, UserConfig

DEFAULT_RESTAURANTS_PATH = "config/restaurants.yaml"

# Public key, safe to hardcode as default
DEFAULT_RESY_API_KEY = "VbWk7s3L4KiK5fzlO7JD3Q5EYolJI7n5"


def _parse_comma_proxies(v: str | list) -> list[ProxyConfig]:
    if isinstance(v, list):
        return [ProxyConfig(url=u) if isinstance(u, str) else u for u in v]
    if not v.strip():
        return []
    return [ProxyConfig(url=u.strip()) for u in v.split(",") if u.strip()]


def _parse_json_users(v: str | list) -> list[UserConfig]:
    if isinstance(v, list):
        return [UserConfig.model_validate(u) if isinstance(u, dict) else u for u in v]
    parsed = json.loads(v)
    return [UserConfig.model_validate(u) for u in parsed]


ProxyList = Annotated[list[ProxyConfig], BeforeValidator(_parse_comma_proxies)]
UserList = Annotated[list[UserConfig], BeforeValidator(_parse_json_users)]


class MonitorSettings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    resy_api_key: str = DEFAULT_RESY_API_KEY
    monitor_proxy_urls: ProxyList = []
    scan_start_seconds_before: int = 15
    scan_interval_ms: int = 3500
    scan_timeout_seconds: int = 120
    use_proxies: bool = False
    ws_host: str = "0.0.0.0"
    ws_port: int = 8765
    log_level: str = "INFO"


class BookingSettings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    resy_api_key: str = DEFAULT_RESY_API_KEY
    booking_proxy_urls: ProxyList = []
    booking_users: UserList = []
    discord_webhook_url: str = ""
    dry_run: bool = False
    monitor_ws_url: str = "ws://localhost:8765"
    log_level: str = "INFO"


def load_restaurants(path: str | None = None) -> list[RestaurantConfig]:
    """Load restaurant configs from YAML file."""
    config_path = path or DEFAULT_RESTAURANTS_PATH
    with open(config_path) as f:
        data = yaml.safe_load(f.read())
    restaurants = data if isinstance(data, list) else data.get("restaurants", [])
    return [RestaurantConfig.model_validate(r) for r in restaurants]
