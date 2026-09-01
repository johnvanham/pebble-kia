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
