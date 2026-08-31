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
    # Floor on car-waking refreshes. A forced refresh inside this window
    # is downgraded to a cached read rather than rejected, so the watch
    # never surfaces an error for pressing the button too eagerly.
    live_force_min_seconds: int = Field(900, alias="LIVE_FORCE_MIN_SECONDS")
    # Transition detector. Runs as a background asyncio task and fires
    # ntfy pushes on state changes. Unset means per-source defaults:
    # DEMO_DETECTOR_SECONDS keeps scenario progression visible, while
    # live wants to stay well inside Kia's tolerance.
    detector_interval_seconds: int | None = Field(None, alias="DETECTOR_INTERVAL_SECONDS")
    # Kia account. Only read when DATA_SOURCE=live. Region and brand are
    # the integers hyundai_kia_connect_api expects:
    # REGIONS {1: EU, 2: CA, 3: US, 4: CN, 5: AU}, BRANDS {1: Kia, 2: Hyundai, 3: Genesis}.
    kia_username: str = Field("", alias="KIA_USERNAME")
    kia_password: str = Field("", alias="KIA_PASSWORD")
    kia_pin: str = Field("", alias="KIA_PIN")
    kia_region: int = Field(1, alias="KIA_REGION")
    kia_brand: int = Field(1, alias="KIA_BRAND")
    kia_language: str = Field("en", alias="KIA_LANGUAGE")
    # SQLite file holding the Kia refresh token and last-known vehicle
    # state, so a restart doesn't cost the watch a cold fetch.
    state_db: Path = Field(Path("state.db"), alias="PROXY_STATE_DB")
    # ntfy notifier. Leave NTFY_URL empty to disable push (transitions
    # are still logged so the detector itself is observable).
    ntfy_url: str = Field("", alias="NTFY_URL")
    ntfy_topic: str = Field("", alias="NTFY_TOPIC")
    ntfy_auth_token: str = Field("", alias="NTFY_AUTH_TOKEN")
    log_level: str = Field("info", alias="LOG_LEVEL")


DEMO_DETECTOR_SECONDS = 20
LIVE_DETECTOR_SECONDS = 300


def detector_interval(settings: Settings) -> int:
    if settings.detector_interval_seconds is not None:
        return settings.detector_interval_seconds
    return (DEMO_DETECTOR_SECONDS if settings.data_source == "demo"
            else LIVE_DETECTOR_SECONDS)


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
