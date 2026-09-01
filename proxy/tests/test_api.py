import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROXY_DIR = Path(__file__).resolve().parent.parent
TOKEN = "test-token-123"


@pytest.fixture
def client(tmp_path, monkeypatch):
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

    from app.main import app
    with TestClient(app) as c:
        yield c


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_health_needs_no_token(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "data_source": "demo"}


def test_vehicles_requires_a_token(client):
    assert client.get("/vehicles").status_code == 401


def test_vehicles_rejects_a_wrong_token(client):
    r = client.get("/vehicles", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 403


def test_vehicles_lists_the_demo_fleet(client):
    r = client.get("/vehicles", headers=auth())
    assert r.status_code == 200
    ids = [v["id"] for v in r.json()["vehicles"]]
    assert "pv5-demo" in ids


def test_status_returns_a_full_payload(client):
    r = client.get("/vehicles/pv5-demo/status", headers=auth())
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "pv5-demo"
    for field in ("soc_pct", "range_km", "is_charging", "plug",
                  "doors_locked", "outside_temp_c", "odo_km",
                  "aux_battery_pct", "updated_at"):
        assert field in body["status"]


def test_status_carries_the_body_and_efficiency_fields(client):
    body = client.get("/vehicles/pv5-demo/status", headers=auth()).json()["status"]
    assert body["charge_limit_ac"] == 80
    assert body["charge_limit_dc"] == 100
    assert body["doors_open"] == 0
    assert body["windows_open"] == 0
    assert body["trunk_open"] is False
    assert body["hood_open"] is False
    assert body["sunroof_open"] is False
    assert body["efficiency_kmpkwh"] == 5.1
    assert body["batt_temp_c"] == 15


def test_the_ev9_demo_exercises_the_open_window_row(client):
    body = client.get("/vehicles/ev9-demo/status", headers=auth()).json()["status"]
    assert body["windows_open"] == 1
    assert body["batt_temp_c"] == 22


def test_scenario_files_predating_the_new_fields_still_load():
    from app.sources.demo import DemoDataSource

    paths = sorted((PROXY_DIR / "scenarios").glob("*.json"))
    assert paths
    for path in paths:
        source = DemoDataSource(path)
        for vehicle in source.list_vehicles():
            status = source.fetch_status(vehicle.id)
            assert status.charge_limit_ac == 0
            assert status.doors_open == 0
            assert status.windows_open == 0
            assert status.sunroof_open is False
            assert status.efficiency_kmpkwh == 0.0
            assert status.batt_temp_c is None


def test_second_status_is_served_from_cache(client):
    client.get("/vehicles/pv5-demo/status", headers=auth())
    r = client.get("/vehicles/pv5-demo/status", headers=auth())
    assert r.json()["from_cache"] is True


def test_refresh_bypasses_the_cache(client):
    client.get("/vehicles/pv5-demo/status", headers=auth())
    r = client.post("/vehicles/pv5-demo/refresh", headers=auth())
    assert r.status_code == 200
    body = r.json()
    assert body["from_cache"] is False
    # Demo has no vehicle to wake, so the force floor is 0 and the
    # forced read always goes through.
    assert body["forced"] is True


def test_force_query_param_matches_the_refresh_route(client):
    client.get("/vehicles/pv5-demo/status", headers=auth())
    r = client.get("/vehicles/pv5-demo/status?force=1", headers=auth())
    assert r.json()["from_cache"] is False


def test_fresh_bypasses_the_cache_without_forcing(client):
    client.get("/vehicles/pv5-demo/status", headers=auth())
    r = client.get("/vehicles/pv5-demo/status?fresh=1", headers=auth())
    body = r.json()
    assert body["from_cache"] is False
    assert body["forced"] is False


def test_fresh_result_serves_the_next_ordinary_read(client):
    client.get("/vehicles/pv5-demo/status?fresh=1", headers=auth())
    r = client.get("/vehicles/pv5-demo/status", headers=auth())
    assert r.json()["from_cache"] is True


def test_fresh_with_force_keeps_force_semantics(client):
    client.get("/vehicles/pv5-demo/status", headers=auth())
    r = client.get("/vehicles/pv5-demo/status?force=1&fresh=1", headers=auth())
    body = r.json()
    assert body["from_cache"] is False
    assert body["forced"] is True


def test_fresh_with_force_still_downgrades_inside_the_floor(client):
    from app.main import app

    # Demo runs with a zero force floor; raise it so the second force
    # lands inside the window and must downgrade to the cache entry,
    # fresh=1 notwithstanding.
    app.state.cache.force_min_interval = 900
    client.get("/vehicles/pv5-demo/status?force=1", headers=auth())
    r = client.get("/vehicles/pv5-demo/status?force=1&fresh=1", headers=auth())
    body = r.json()
    assert body["from_cache"] is True
    assert body["forced"] is False


def test_unknown_vehicle_is_a_404(client):
    r = client.get("/vehicles/not-a-car/status", headers=auth())
    assert r.status_code == 404


def test_status_is_persisted_for_the_next_boot(client, tmp_path):
    from app.store import StateStore

    body = client.get("/vehicles/pv5-demo/status", headers=auth()).json()

    stored = StateStore(tmp_path / "state.db").load_statuses()
    assert "pv5-demo" in stored
    status, _ = stored["pv5-demo"]
    assert status.odo_km == body["status"]["odo_km"]


def test_detector_primes_the_cache_before_the_first_client(client):
    # The detector's first tick runs at startup and goes through the
    # same cache, so the watch's first request is already a hit rather
    # than a second upstream call.
    assert client.get("/vehicles/pv5-demo/status",
                      headers=auth()).json()["from_cache"] is True


KIA_ERRORS = [
    ("ConsentRequiredError", 503),
    ("AuthenticationOTPRequired", 503),
    ("AuthenticationError", 502),
    ("RateLimitingError", 429),
    ("NoDataFound", 404),
    ("DuplicateRequestError", 503),
    ("RequestTimeoutError", 503),
    ("ServiceTemporaryUnavailable", 503),
]


@pytest.mark.parametrize("exc_name,expected", KIA_ERRORS)
def test_kia_errors_map_to_actionable_statuses(client, exc_name, expected):
    """The companion turns these statuses into readable watch strings, so a
    Kia failure must not arrive as a blanket 500."""
    from hyundai_kia_connect_api import exceptions

    from app.main import app

    exc = getattr(exceptions, exc_name)

    def boom(vehicle_id, *, force=False):
        raise exc("upstream said no")

    app.state.source.fetch_status = boom
    # A zero TTL means nothing is ever fresh, so the request cannot be
    # served from whatever the detector last put in the cache.
    app.state.cache.min_interval = 0

    r = client.get("/vehicles/pv5-demo/status", headers=auth())
    assert r.status_code == expected
    assert r.json()["detail"]


def test_unrecognised_kia_error_is_a_502(client):
    from hyundai_kia_connect_api.exceptions import HyundaiKiaException

    from app.main import app

    def boom(vehicle_id, *, force=False):
        raise HyundaiKiaException("something new")

    app.state.source.fetch_status = boom
    # A zero TTL means nothing is ever fresh, so the request cannot be
    # served from whatever the detector last put in the cache.
    app.state.cache.min_interval = 0

    r = client.get("/vehicles/pv5-demo/status", headers=auth())
    assert r.status_code == 502
