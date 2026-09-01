import logging
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The QR directory defaults relative to this package, not to the process
# working directory: the proxy gets launched from proxy/, from the repo
# root, and from a systemd unit with any WorkingDirectory at all, and a
# file holding a live credential must not follow the caller around.
_PROXY_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # .env sits beside the package, like setup_qr_dir below: both should
    # resolve the same wherever the process is started from.
    model_config = SettingsConfigDict(env_file=_PROXY_DIR / ".env",
                                      env_file_encoding="utf-8",
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
    # Remote commands. The proxy is read-only unless this is set — the
    # actions endpoint answers 403. Deliberately opt-in: commands are a
    # separate risk surface from reads, and a forked deployment should
    # have to choose them.
    enable_commands: bool = Field(False, alias="ENABLE_COMMANDS")
    # Floor between remote commands per vehicle. Unlike the force
    # floor there is no cached equivalent to downgrade to, so a command
    # inside the window is refused with 429 rather than absorbed.
    command_min_seconds: int = Field(10, alias="COMMAND_MIN_SECONDS")
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
    # Directory the startup QR images are written to, so the owner can
    # scan the bearer token off a monitor instead of typing 64 hex
    # characters into a phone. Never served over HTTP — the files hold a
    # live credential. Empty disables the feature.
    setup_qr_dir: Path | None = Field(_PROXY_DIR / "setup", alias="SETUP_QR_DIR")
    # Also render the token QR into the log. That block is a
    # machine-decodable copy of the bearer token, and logs outlive token
    # rotation and get shipped and pasted around, so it is off unless
    # asked for.
    setup_qr_log: bool = Field(False, alias="SETUP_QR_LOG")
    # The URL the *phone* should use. The proxy cannot work out its own
    # externally-reachable address, so leaving this empty skips the URL
    # QR rather than encoding a guess.
    proxy_public_url: str = Field("", alias="PROXY_PUBLIC_URL")
    log_level: str = Field("info", alias="LOG_LEVEL")

    @field_validator("detector_interval_seconds", mode="before")
    @classmethod
    def _blank_means_default(cls, v):
        # Both .env and docker-compose express "unset" as an empty string
        # rather than an absent key, and an empty string is not an int.
        return None if v == "" else v

    @field_validator("setup_qr_dir", mode="before")
    @classmethod
    def _blank_means_disabled(cls, v):
        # Path("") is Path("."), which would scatter credential images
        # across the working directory instead of switching them off.
        if v is None or v == "":
            return None
        # Nothing expands ~ for us here, so a bare Path would create a
        # directory literally named "~".
        return Path(v).expanduser()


_LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")


def configure_logging(settings: Settings) -> None:
    """Apply LOG_LEVEL to this package's loggers, and only to them.

    Raising the root level instead would also uncork
    hyundai_kia_connect_api's DEBUG output, which prints whole Kia
    payloads — VIN and GPS coordinates included. Root stays where it is,
    so third-party DEBUG remains suppressed even at LOG_LEVEL=debug.
    uvicorn configures its own loggers and is likewise unaffected.
    """
    wanted = settings.log_level.strip().upper()
    level = wanted if wanted in _LOG_LEVELS else "INFO"

    log = logging.getLogger(__package__)
    # Lifespan runs once per app start, and the test suite starts many;
    # a second handler would double every line.
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        log.addHandler(handler)
    log.setLevel(level)

    if level != wanted:
        log.warning("unrecognised LOG_LEVEL %r — using info", settings.log_level)


DEMO_DETECTOR_SECONDS = 20
LIVE_DETECTOR_SECONDS = 300


def detector_interval(settings: Settings) -> int:
    if settings.detector_interval_seconds is not None:
        return settings.detector_interval_seconds
    return (DEMO_DETECTOR_SECONDS if settings.data_source == "demo"
            else LIVE_DETECTOR_SECONDS)


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
