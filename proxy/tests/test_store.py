import json
import sqlite3
from datetime import datetime, timezone

from app.models import VehicleStatus
from app.store import StateStore


def make_status(**overrides) -> VehicleStatus:
    fields = dict(
        soc_pct=64,
        range_km=212,
        is_charging=True,
        charge_kw=6.6,
        charge_eta_min=95,
        plug="ac",
        doors_locked=True,
        outside_temp_c=18,
        odo_km=14231,
        aux_battery_pct=87,
        is_climate_on=False,
        updated_at=datetime(2026, 8, 31, 21, 14, 5, tzinfo=timezone.utc),
    )
    fields.update(overrides)
    return VehicleStatus(**fields)


def test_load_on_empty_db(tmp_path):
    store = StateStore(tmp_path / "state.db")
    assert store.load_token() is None
    assert store.load_statuses() == {}


def test_creates_parent_directory(tmp_path):
    store = StateStore(tmp_path / "nested" / "dir" / "state.db")
    store.save_token({"access_token": "a"})
    assert store.load_token() == {"access_token": "a"}


def test_token_round_trip(tmp_path):
    store = StateStore(tmp_path / "state.db")
    token = {
        "access_token": "abc123",
        "refresh_token": "def456",
        "device_id": "dev-1",
        "valid_until": "2026-09-01T10:00:00+00:00",
    }
    store.save_token(token)
    assert store.load_token() == token


def test_token_overwrite_replaces(tmp_path):
    path = tmp_path / "state.db"
    store = StateStore(path)
    store.save_token({"access_token": "old"})
    store.save_token({"access_token": "new"})
    assert store.load_token() == {"access_token": "new"}

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM kv").fetchone()[0] == 1


def test_token_secrets_never_reach_disk(tmp_path):
    path = tmp_path / "state.db"
    store = StateStore(path)
    store.save_token(
        {
            "access_token": "abc123",
            "refresh_token": "def456",
            "password": "hunter2",
            "pin": "1234",
        }
    )

    with sqlite3.connect(path) as conn:
        raw = conn.execute("SELECT value FROM kv").fetchone()[0]
    assert "hunter2" not in raw
    assert "1234" not in raw
    assert "password" not in raw
    assert "pin" not in raw

    loaded = store.load_token()
    assert loaded == {"access_token": "abc123", "refresh_token": "def456"}


def test_token_datetime_serialises(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.save_token({"valid_until": datetime(2026, 9, 1, 10, 0, 0)})
    assert store.load_token() == {"valid_until": "2026-09-01 10:00:00"}


def test_status_round_trip(tmp_path):
    store = StateStore(tmp_path / "state.db")
    status = make_status()
    store.save_status("veh-1", status, 1756673645.5)

    statuses = store.load_statuses()
    assert set(statuses) == {"veh-1"}
    loaded, wall = statuses["veh-1"]
    assert wall == 1756673645.5
    assert loaded == status
    assert loaded.updated_at == status.updated_at


def test_status_overwrite_replaces(tmp_path):
    path = tmp_path / "state.db"
    store = StateStore(path)
    store.save_status("veh-1", make_status(soc_pct=40), 100.0)
    store.save_status("veh-1", make_status(soc_pct=80), 200.0)

    statuses = store.load_statuses()
    assert len(statuses) == 1
    loaded, wall = statuses["veh-1"]
    assert loaded.soc_pct == 80
    assert wall == 200.0

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM vehicle_status").fetchone()[0] == 1


def test_multiple_vehicles(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.save_status("veh-1", make_status(soc_pct=40), 100.0)
    store.save_status("veh-2", make_status(soc_pct=90), 150.0)

    statuses = store.load_statuses()
    assert statuses["veh-1"][0].soc_pct == 40
    assert statuses["veh-2"][0].soc_pct == 90


def test_corrupt_status_row_is_skipped(tmp_path):
    path = tmp_path / "state.db"
    store = StateStore(path)
    store.save_status("veh-good", make_status(), 100.0)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO vehicle_status (id, status_json, wall_fetched_at) "
            "VALUES (?, ?, ?)",
            ("veh-bad", "{not json", 200.0),
        )
        conn.execute(
            "INSERT INTO vehicle_status (id, status_json, wall_fetched_at) "
            "VALUES (?, ?, ?)",
            ("veh-partial", json.dumps({"soc_pct": 50}), 300.0),
        )

    statuses = store.load_statuses()
    assert set(statuses) == {"veh-good"}


def test_corrupt_token_row_is_discarded(tmp_path):
    path = tmp_path / "state.db"
    store = StateStore(path)
    store.save_token({"access_token": "abc"})
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE kv SET value = ? WHERE key = 'kia_token'", ("{not json",))

    assert store.load_token() is None


def test_reopening_sees_existing_data(tmp_path):
    path = tmp_path / "state.db"
    first = StateStore(path)
    first.save_token({"access_token": "abc"})
    first.save_status("veh-1", make_status(), 100.0)

    second = StateStore(path)
    assert second.load_token() == {"access_token": "abc"}
    assert second.load_statuses()["veh-1"][0] == make_status()
