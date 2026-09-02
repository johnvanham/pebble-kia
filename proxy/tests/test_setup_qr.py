import logging
import os
from pathlib import Path

import pytest
import segno
from fastapi.testclient import TestClient

from app import setup_qr
from app.config import Settings
from app.setup_qr import emit_setup_qr

TOKEN = "a" * 64
URL = "https://kia.example.com"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PROXY_DIR = Path(__file__).resolve().parent.parent


def _has_qr_block(text: str) -> bool:
    return any(block in text for block in "\u2580\u2584\u2588")


def make_settings(**overrides) -> Settings:
    fields = {
        "PROXY_BEARER_TOKEN": TOKEN,
        "PROXY_PUBLIC_URL": "",
        "DATA_SOURCE": "demo",
    }
    fields.update(overrides)
    return Settings(**fields)  # type: ignore[call-arg]


@pytest.fixture
def payloads(monkeypatch):
    """Every string handed to segno, in order. segno cannot decode its own
    output, so this is how we check what actually got encoded."""
    seen = []
    real_make = segno.make_qr

    def spy(payload, **kwargs):
        seen.append(payload)
        return real_make(payload, **kwargs)

    monkeypatch.setattr(setup_qr.segno, "make_qr", spy)
    return seen


def test_token_image_is_written(tmp_path):
    emit_setup_qr(make_settings(SETUP_QR_DIR=tmp_path / "setup"))

    png = tmp_path / "setup" / "bearer-token.png"
    assert png.read_bytes()[:8] == PNG_MAGIC
    assert png.stat().st_size > 0


def test_token_image_is_owner_only(tmp_path):
    directory = tmp_path / "setup"
    emit_setup_qr(make_settings(SETUP_QR_DIR=directory))

    assert oct(directory.stat().st_mode)[-3:] == "700"
    assert oct((directory / "bearer-token.png").stat().st_mode)[-3:] == "600"


def test_existing_world_readable_files_are_tightened(tmp_path):
    directory = tmp_path / "setup"
    directory.mkdir(mode=0o755)
    (directory / "bearer-token.png").write_bytes(b"stale")
    os.chmod(directory / "bearer-token.png", 0o644)

    emit_setup_qr(make_settings(SETUP_QR_DIR=directory))

    assert oct(directory.stat().st_mode)[-3:] == "700"
    assert oct((directory / "bearer-token.png").stat().st_mode)[-3:] == "600"


def test_token_payload_is_exactly_the_token(tmp_path, payloads):
    emit_setup_qr(make_settings(SETUP_QR_DIR=tmp_path / "setup"))

    assert payloads
    assert set(payloads) == {TOKEN}


def test_a_failed_write_removes_the_previous_image(tmp_path, monkeypatch):
    # A stale image still scans, and what it hands the phone is the old
    # token — worse than no image at all.
    directory = tmp_path / "setup"
    emit_setup_qr(make_settings(SETUP_QR_DIR=directory))

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(setup_qr, "_encode", boom)
    emit_setup_qr(make_settings(SETUP_QR_DIR=directory))

    assert not (directory / "bearer-token.png").exists()


def test_the_token_qr_is_not_logged_by_default(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    emit_setup_qr(make_settings(SETUP_QR_DIR=tmp_path / "setup"))

    assert not _has_qr_block(caplog.text)


def test_setup_qr_log_puts_the_token_qr_in_the_log(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    emit_setup_qr(make_settings(SETUP_QR_DIR=tmp_path / "setup", SETUP_QR_LOG="1"))

    assert _has_qr_block(caplog.text)


def test_a_home_relative_dir_is_expanded(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)

    emit_setup_qr(make_settings(SETUP_QR_DIR="~/qr"))

    assert (home / "qr" / "bearer-token.png").exists()
    assert not (tmp_path / "~").exists()


def test_the_default_dir_does_not_follow_the_working_directory(tmp_path, monkeypatch):
    # Launching from the repo root used to drop the credential image at
    # <repo>/setup, outside proxy/.gitignore.
    monkeypatch.delenv("SETUP_QR_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    assert make_settings().setup_qr_dir == PROXY_DIR / "setup"


def test_a_short_token_is_not_encoded_as_a_micro_qr():
    # segno.make() picks a Micro QR for short payloads, and neither phone
    # camera apps nor zbar decode those.
    assert not setup_qr._encode("short-token").is_micro


def test_url_image_is_skipped_when_public_url_is_unset(tmp_path, payloads):
    emit_setup_qr(make_settings(SETUP_QR_DIR=tmp_path / "setup"))

    assert not (tmp_path / "setup" / "proxy-url.png").exists()
    assert URL not in payloads


def test_url_image_is_written_when_public_url_is_set(tmp_path, payloads):
    emit_setup_qr(
        make_settings(SETUP_QR_DIR=tmp_path / "setup", PROXY_PUBLIC_URL=URL)
    )

    png = tmp_path / "setup" / "proxy-url.png"
    assert png.read_bytes()[:8] == PNG_MAGIC
    assert oct(png.stat().st_mode)[-3:] == "600"
    assert payloads.count(URL) == 1


def test_empty_dir_setting_disables_the_feature(tmp_path, payloads, monkeypatch):
    monkeypatch.chdir(tmp_path)
    emit_setup_qr(make_settings(SETUP_QR_DIR=""))

    assert payloads == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_directory_does_not_raise(tmp_path):
    readonly = tmp_path / "readonly"
    readonly.mkdir(mode=0o500)

    emit_setup_qr(make_settings(SETUP_QR_DIR=readonly / "setup"))

    assert not (readonly / "setup").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_directory_does_not_stop_startup(tmp_path, monkeypatch):
    readonly = tmp_path / "readonly"
    readonly.mkdir(mode=0o500)

    monkeypatch.setenv("PROXY_BEARER_TOKEN", TOKEN)
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("DEMO_DATA_FILE", str(PROXY_DIR / "demo-data.json"))
    monkeypatch.setenv("PROXY_STATE_DB", str(tmp_path / "state.db"))
    monkeypatch.setenv("SETUP_QR_DIR", str(readonly / "setup"))

    from app.main import app
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
