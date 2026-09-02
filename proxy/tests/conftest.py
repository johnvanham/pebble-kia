import pytest


@pytest.fixture(autouse=True)
def setup_qr_dir_is_temporary(tmp_path, monkeypatch):
    # Booting the app writes the bearer-token QR, and the default
    # location is the owner's real proxy/setup — a test run would
    # overwrite their live image with a test token. Autouse so it holds
    # for tests written later too; a test that cares about the directory
    # still passes its own.
    monkeypatch.setenv("SETUP_QR_DIR", str(tmp_path / "setup-qr"))
    # Pinned too, because the docs tell the owner to turn it on, and an
    # ambient SETUP_QR_LOG=1 would otherwise fail the default-off test.
    monkeypatch.delenv("SETUP_QR_LOG", raising=False)


class FakeClock:
    """Stands in for the `time` module inside app.cache.

    The refresh window and the charging window are measured in minutes,
    so the alternative is a test suite that sleeps for a quarter of an
    hour.
    """

    def __init__(self) -> None:
        self.mono = 1000.0
        self.wall = 1_700_000_000.0

    def monotonic(self) -> float:
        return self.mono

    def time(self) -> float:
        return self.wall

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.wall += seconds


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr("app.cache.time", c)
    return c
