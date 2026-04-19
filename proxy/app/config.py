from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8",
                                      extra="ignore")

    bearer_token: str = Field(..., alias="PROXY_BEARER_TOKEN")
    data_source: Literal["demo", "live"] = Field("demo", alias="DATA_SOURCE")
    demo_data_file: Path = Field(Path("demo-data.json"), alias="DEMO_DATA_FILE")
    live_refresh_min_seconds: int = Field(600, alias="LIVE_REFRESH_MIN_SECONDS")
    # Separate cache interval for the demo source — scenario-driven demos
    # want short TTLs so polling reflects progression, while live wants
    # long TTLs to protect the 12V battery.
    demo_refresh_min_seconds: int = Field(5, alias="DEMO_REFRESH_MIN_SECONDS")
    # Transition detector. Runs as a background asyncio task and fires
    # ntfy pushes on state changes. Interval is independent of the cache
    # TTL — it wants frequent enough to catch brief transitions.
    detector_interval_seconds: int = Field(20, alias="DETECTOR_INTERVAL_SECONDS")
    # ntfy notifier. Leave NTFY_URL empty to disable push (transitions
    # are still logged so the detector itself is observable).
    ntfy_url: str = Field("", alias="NTFY_URL")
    ntfy_topic: str = Field("", alias="NTFY_TOPIC")
    ntfy_auth_token: str = Field("", alias="NTFY_AUTH_TOKEN")
    log_level: str = Field("info", alias="LOG_LEVEL")


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
