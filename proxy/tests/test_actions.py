from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

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
    assert "hazard_lights" in r.json()["detail"]


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


def test_port_and_hazard_actions_succeed_without_visible_change(client):
    before = client.get("/vehicles/pv5-demo/status?fresh=1", headers=auth()).json()
    for action in ("open_charge_port", "close_charge_port", "hazard_lights"):
        r = client.post(f"/vehicles/pv5-demo/actions/{action}", headers=auth())
        assert r.status_code == 200
    after = client.get("/vehicles/pv5-demo/status?fresh=1", headers=auth()).json()
    assert after["status"]["doors_locked"] == before["status"]["doors_locked"]
    assert after["status"]["is_charging"] == before["status"]["is_charging"]


def test_valet_actions_are_gone(client):
    # Dropped in phase 9: the endpoint is pre-CCS2, the library never
    # populates valet_mode_active, and supports_valet_mode is a
    # hard-coded per-region constant rather than anything the PV5 said.
    for action in ("start_valet", "stop_valet"):
        r = client.post(f"/vehicles/pv5-demo/actions/{action}", headers=auth())
        assert r.status_code == 400


def test_defrost_lights_the_de_icing_surfaces(client):
    client.post("/vehicles/pv5-demo/actions/start_defrost", headers=auth())
    s = client.get("/vehicles/pv5-demo/status?fresh=1", headers=auth()).json()["status"]
    assert s["is_climate_on"] is True
    assert (s["defrost_on"], s["rear_defrost_on"], s["wheel_heat_on"]) == (
        True, True, True)

    client.post("/vehicles/pv5-demo/actions/stop_climate", headers=auth())
    s = client.get("/vehicles/pv5-demo/status?fresh=1", headers=auth()).json()["status"]
    assert s["is_climate_on"] is False
    assert (s["defrost_on"], s["rear_defrost_on"], s["wheel_heat_on"]) == (
        False, False, False)


def test_plain_climate_leaves_the_heaters_alone(client):
    client.post("/vehicles/pv5-demo/actions/start_climate", headers=auth())
    s = client.get("/vehicles/pv5-demo/status?fresh=1", headers=auth()).json()["status"]
    assert s["is_climate_on"] is True
    assert s["defrost_on"] is False


def test_set_charge_limit_writes_both_targets(client):
    r = client.post("/vehicles/pv5-demo/actions/set_charge_limit?ac=70&dc=90",
                    headers=auth())
    assert r.status_code == 200
    s = client.get("/vehicles/pv5-demo/status?fresh=1", headers=auth()).json()["status"]
    assert (s["charge_limit_ac"], s["charge_limit_dc"]) == (70, 90)


def test_set_charge_limit_needs_both_values(client):
    r = client.post("/vehicles/pv5-demo/actions/set_charge_limit?ac=70",
                    headers=auth())
    assert r.status_code == 400
    assert "dc" in r.json()["detail"]


@pytest.mark.parametrize("query", ["ac=85&dc=90", "ac=0&dc=90", "ac=70&dc=110"])
def test_set_charge_limit_rejects_values_the_car_cannot_take(client, query):
    r = client.post(f"/vehicles/pv5-demo/actions/set_charge_limit?{query}",
                    headers=auth())
    assert r.status_code == 400


def test_parameters_on_an_action_that_takes_none_are_rejected(client):
    # Silently ignoring them would let a watch that mis-sends a limit
    # believe it landed.
    r = client.post("/vehicles/pv5-demo/actions/lock?ac=80", headers=auth())
    assert r.status_code == 400


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


def test_live_commands_without_a_pin_refuse_to_start():
    # A CCS2 command needs a control token minted from the PIN, so this
    # combination can only ever fail at Kia — better at boot than at the
    # owner's first unlock.
    from app.config import Settings

    with pytest.raises(ValidationError, match="KIA_PIN"):
        Settings(  # type: ignore[call-arg]
            PROXY_BEARER_TOKEN="x",
            DATA_SOURCE="live",
            ENABLE_COMMANDS="1",
            KIA_PIN="",
            _env_file=None,
        )


def test_live_reads_without_a_pin_are_fine():
    from app.config import Settings

    settings = Settings(  # type: ignore[call-arg]
        PROXY_BEARER_TOKEN="x",
        DATA_SOURCE="live",
        ENABLE_COMMANDS="0",
        KIA_PIN="",
        _env_file=None,
    )
    assert settings.kia_pin == ""
