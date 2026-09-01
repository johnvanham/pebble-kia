from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROXY_DIR = Path(__file__).resolve().parent.parent
TOKEN = "test-token-123"


def _demo_env(tmp_path, monkeypatch):
    # Explicit env beats any .env the owner has sitting in proxy/ with
    # real Kia credentials in it — these tests must never touch live.
    monkeypatch.setenv("PROXY_BEARER_TOKEN", TOKEN)
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("DEMO_DATA_FILE", str(PROXY_DIR / "demo-data.json"))
    monkeypatch.setenv("PROXY_STATE_DB", str(tmp_path / "state.db"))
    monkeypatch.setenv("DEMO_REFRESH_MIN_SECONDS", "300")
    monkeypatch.setenv("DETECTOR_INTERVAL_SECONDS", "3600")
    monkeypatch.delenv("NTFY_URL", raising=False)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)


@pytest.fixture
def client(tmp_path, monkeypatch):
    _demo_env(tmp_path, monkeypatch)
    monkeypatch.setenv("ENABLE_COMMANDS", "1")
    # Zero floor so a test can send several commands back to back; the
    # throttle test raises it on the live app object instead.
    monkeypatch.setenv("COMMAND_MIN_SECONDS", "0")

    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def readonly_client(tmp_path, monkeypatch):
    _demo_env(tmp_path, monkeypatch)
    # Set it, don't just delete it: pydantic-settings falls back to the
    # owner's proxy/.env when the var is absent, so delenv alone would
    # let a real .env (which enables commands on this box) decide the
    # gate this test exists to pin.
    monkeypatch.setenv("ENABLE_COMMANDS", "0")

    from app.main import app
    with TestClient(app) as c:
        yield c


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_commands_are_disabled_by_default(readonly_client):
    r = readonly_client.post("/vehicles/pv5-demo/actions/unlock", headers=auth())
    assert r.status_code == 403
    assert r.json()["detail"] == (
        "Commands disabled - set ENABLE_COMMANDS=1 on the proxy"
    )


def test_commands_default_off_in_code(monkeypatch):
    # The integration test above sets ENABLE_COMMANDS=0 explicitly, so it
    # would still pass if the Field default flipped to True. Pin the
    # actual code default here, past both the process env and the .env
    # file, so a regression can't hide behind either.
    monkeypatch.delenv("ENABLE_COMMANDS", raising=False)
    from app.config import Settings

    settings = Settings(PROXY_BEARER_TOKEN="x", _env_file=None)  # type: ignore[call-arg]
    assert settings.enable_commands is False


def test_actions_require_a_token(client):
    assert client.post("/vehicles/pv5-demo/actions/unlock").status_code == 401


def test_unknown_action_is_a_400_listing_valid_names(client):
    r = client.post("/vehicles/pv5-demo/actions/dance", headers=auth())
    assert r.status_code == 400
    assert "unlock" in r.json()["detail"]
    assert "start_valet" in r.json()["detail"]


def test_unknown_vehicle_is_a_404(client):
    r = client.post("/vehicles/not-a-car/actions/unlock", headers=auth())
    assert r.status_code == 404


def test_unlock_reports_sent_and_flips_the_demo_doors(client):
    r = client.post("/vehicles/pv5-demo/actions/unlock", headers=auth())
    assert r.status_code == 200
    assert r.json() == {"id": "pv5-demo", "action": "unlock", "status": "sent"}

    body = client.get("/vehicles/pv5-demo/status?fresh=1", headers=auth()).json()
    assert body["status"]["doors_locked"] is False

    client.post("/vehicles/pv5-demo/actions/lock", headers=auth())
    body = client.get("/vehicles/pv5-demo/status?fresh=1", headers=auth()).json()
    assert body["status"]["doors_locked"] is True


def test_start_charge_shows_up_in_the_demo_status(client):
    client.post("/vehicles/pv5-demo/actions/start_charge", headers=auth())
    body = client.get("/vehicles/pv5-demo/status?fresh=1", headers=auth()).json()
    # The demo PV5 starts unplugged, so start_charge also plugs it in.
    assert body["status"]["is_charging"] is True
    assert body["status"]["charge_kw"] == 7.4
    assert body["status"]["plug"] == "ac"

    client.post("/vehicles/pv5-demo/actions/stop_charge", headers=auth())
    body = client.get("/vehicles/pv5-demo/status?fresh=1", headers=auth()).json()
    assert body["status"]["is_charging"] is False
    assert body["status"]["charge_kw"] == 0.0


def test_climate_actions_flip_the_demo_flag(client):
    client.post("/vehicles/pv5-demo/actions/start_climate", headers=auth())
    body = client.get("/vehicles/pv5-demo/status?fresh=1", headers=auth()).json()
    assert body["status"]["is_climate_on"] is True

    client.post("/vehicles/pv5-demo/actions/stop_climate", headers=auth())
    body = client.get("/vehicles/pv5-demo/status?fresh=1", headers=auth()).json()
    assert body["status"]["is_climate_on"] is False


def test_port_and_valet_actions_succeed_without_visible_change(client):
    before = client.get("/vehicles/pv5-demo/status?fresh=1", headers=auth()).json()
    for action in ("open_charge_port", "close_charge_port",
                   "start_valet", "stop_valet"):
        r = client.post(f"/vehicles/pv5-demo/actions/{action}", headers=auth())
        assert r.status_code == 200
    after = client.get("/vehicles/pv5-demo/status?fresh=1", headers=auth()).json()
    assert after["status"]["doors_locked"] == before["status"]["doors_locked"]
    assert after["status"]["is_charging"] == before["status"]["is_charging"]


def test_throttle_refuses_a_rapid_second_command(client):
    from app.main import app

    # The fixture runs with a zero floor; raise it so the second
    # command lands inside the window.
    app.state.command_throttle.min_interval = 600
    r = client.post("/vehicles/pv5-demo/actions/lock", headers=auth())
    assert r.status_code == 200

    r = client.post("/vehicles/pv5-demo/actions/unlock", headers=auth())
    assert r.status_code == 429
    assert "retry in" in r.json()["detail"]

    # Dropping the floor back to zero is the window elapsing.
    app.state.command_throttle.min_interval = 0
    r = client.post("/vehicles/pv5-demo/actions/unlock", headers=auth())
    assert r.status_code == 200


def test_a_refused_command_does_not_stamp_the_throttle(client):
    from app.main import app

    app.state.command_throttle.min_interval = 600
    r = client.post("/vehicles/not-a-car/actions/lock", headers=auth())
    assert r.status_code == 404

    # The 404 above must not have started the window for this id.
    r = client.post("/vehicles/not-a-car/actions/dance", headers=auth())
    assert r.status_code == 400
    assert app.state.command_throttle.seconds_until_allowed("not-a-car") == 0.0
