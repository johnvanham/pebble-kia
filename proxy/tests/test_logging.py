import logging

from app.config import Settings, configure_logging

THIRD_PARTY = "hyundai_kia_connect_api.ApiImpl"


def make_settings(level: str) -> Settings:
    return Settings(  # type: ignore[call-arg]
        PROXY_BEARER_TOKEN="t" * 64,
        DATA_SOURCE="demo",
        LOG_LEVEL=level,
    )


def test_debug_does_not_uncork_third_party_loggers(caplog):
    # hyundai_kia_connect_api logs whole Kia payloads at DEBUG, VIN and
    # GPS included, and this project redacts both everywhere else.
    configure_logging(make_settings("debug"))

    assert logging.getLogger("app").getEffectiveLevel() == logging.DEBUG
    assert logging.getLogger(THIRD_PARTY).getEffectiveLevel() == logging.WARNING
    assert not logging.getLogger(THIRD_PARTY).isEnabledFor(logging.DEBUG)


def test_an_unrecognised_level_falls_back_to_info(caplog):
    caplog.set_level(logging.WARNING)
    configure_logging(make_settings("verbose"))

    assert logging.getLogger("app").level == logging.INFO
    assert "unrecognised LOG_LEVEL" in caplog.text


def test_an_empty_level_does_not_raise(caplog):
    configure_logging(make_settings(""))

    assert logging.getLogger("app").level == logging.INFO


def test_configuring_twice_does_not_duplicate_handlers():
    settings = make_settings("info")
    configure_logging(settings)
    before = len(logging.getLogger("app").handlers)
    configure_logging(settings)

    assert len(logging.getLogger("app").handlers) == before
