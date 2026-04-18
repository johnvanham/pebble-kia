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
    log_level: str = Field("info", alias="LOG_LEVEL")


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
