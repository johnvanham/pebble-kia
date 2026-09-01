from datetime import datetime, timezone

import pytest

from app.cache import StatusCache
from app.models import VehicleStatus


class FakeClock:
    """Stands in for the `time` module inside app.cache.

    The force floor and TTL are measured in minutes, so the alternative
    is a test suite that sleeps for a quarter of an hour.
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


class FakeFetch:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def __call__(self, force: bool) -> VehicleStatus:
        self.calls.append(force)
        return _status(soc=len(self.calls))


def _status(soc: int = 50) -> VehicleStatus:
    return VehicleStatus(
        soc_pct=soc,
        range_km=200,
        is_charging=False,
        plug="unplugged",
        doors_locked=True,
        outside_temp_c=18,
        odo_km=12345,
        aux_battery_pct=87,
        updated_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr("app.cache.time", c)
    return c


def test_cold_miss_then_warm_hit(clock):
    cache = StatusCache(min_interval_seconds=600)
    fetch = FakeFetch()

    status, wall, from_cache, forced = cache.get_or_fetch("v1", fetch, force=False)
    assert (from_cache, forced) == (False, False)
    assert wall == clock.wall

    clock.advance(60)
    again, wall2, from_cache2, _ = cache.get_or_fetch("v1", fetch, force=False)
    assert from_cache2 is True
    assert again is status
    assert wall2 == wall
    assert fetch.calls == [False]


def test_ttl_expiry_refetches(clock):
    cache = StatusCache(min_interval_seconds=600)
    fetch = FakeFetch()

    cache.get_or_fetch("v1", fetch, force=False)
    clock.advance(601)
    _, _, from_cache, _ = cache.get_or_fetch("v1", fetch, force=False)

    assert from_cache is False
    assert fetch.calls == [False, False]


def test_force_bypasses_fresh_entry(clock):
    cache = StatusCache(min_interval_seconds=600)
    fetch = FakeFetch()

    cache.get_or_fetch("v1", fetch, force=False)
    clock.advance(1)
    _, _, from_cache, forced = cache.get_or_fetch("v1", fetch, force=True)

    assert (from_cache, forced) == (False, True)
    assert fetch.calls == [False, True]


def test_force_floor_downgrades_rapid_second_force(clock):
    cache = StatusCache(min_interval_seconds=600, force_min_interval_seconds=900)
    fetch = FakeFetch()

    _, _, _, first_forced = cache.get_or_fetch("v1", fetch, force=True)
    assert first_forced is True

    clock.advance(60)
    _, _, from_cache, forced = cache.get_or_fetch("v1", fetch, force=True)

    # Downgraded to the fresh cache entry, not refused and not a wake.
    assert (from_cache, forced) == (True, False)
    assert fetch.calls == [True]


def test_force_floor_downgrade_fetches_unforced_when_stale(clock):
    cache = StatusCache(min_interval_seconds=60, force_min_interval_seconds=900)
    fetch = FakeFetch()

    cache.get_or_fetch("v1", fetch, force=True)
    clock.advance(120)
    _, _, from_cache, forced = cache.get_or_fetch("v1", fetch, force=True)

    assert (from_cache, forced) == (False, False)
    assert fetch.calls == [True, False]


def test_force_floor_expires(clock):
    cache = StatusCache(min_interval_seconds=600, force_min_interval_seconds=900)
    fetch = FakeFetch()

    cache.get_or_fetch("v1", fetch, force=True)
    clock.advance(900)
    _, _, _, forced = cache.get_or_fetch("v1", fetch, force=True)

    assert forced is True
    assert fetch.calls == [True, True]


def test_zero_force_floor_never_downgrades(clock):
    cache = StatusCache(min_interval_seconds=600)
    fetch = FakeFetch()

    for _ in range(3):
        _, _, from_cache, forced = cache.get_or_fetch("v1", fetch, force=True)
        assert (from_cache, forced) == (False, True)

    assert fetch.calls == [True, True, True]


def test_force_floor_is_per_vehicle(clock):
    cache = StatusCache(min_interval_seconds=600, force_min_interval_seconds=900)
    fetch = FakeFetch()

    cache.get_or_fetch("v1", fetch, force=True)
    _, _, _, forced = cache.get_or_fetch("v2", fetch, force=True)

    assert forced is True
    assert fetch.calls == [True, True]


def test_warm_preserves_age(clock):
    cache = StatusCache(min_interval_seconds=600)
    fetch = FakeFetch()
    fresh_status = _status(soc=11)
    stale_status = _status(soc=22)

    cache.warm({
        "fresh": (fresh_status, clock.wall - 60),
        "stale": (stale_status, clock.wall - 3600),
    })

    got, wall, from_cache, _ = cache.get_or_fetch("fresh", fetch, force=False)
    assert from_cache is True
    assert got is fresh_status
    assert wall == clock.wall - 60
    assert fetch.calls == []

    _, _, from_cache, _ = cache.get_or_fetch("stale", fetch, force=False)
    assert from_cache is False
    assert fetch.calls == [False]


def test_warm_does_not_count_as_a_forced_fetch(clock):
    cache = StatusCache(min_interval_seconds=600, force_min_interval_seconds=900)
    fetch = FakeFetch()

    cache.warm({"v1": (_status(), clock.wall)})
    _, _, _, forced = cache.get_or_fetch("v1", fetch, force=True)

    assert forced is True
    assert fetch.calls == [True]


def test_on_store_fires_only_on_real_fetches(clock):
    stored: list[tuple[str, int, float]] = []
    cache = StatusCache(
        min_interval_seconds=600,
        on_store=lambda vid, st, wall: stored.append((vid, st.soc_pct, wall)),
    )
    fetch = FakeFetch()

    cache.get_or_fetch("v1", fetch, force=False)
    assert stored == [("v1", 1, clock.wall)]

    clock.advance(1)
    cache.get_or_fetch("v1", fetch, force=False)
    assert len(stored) == 1

    clock.advance(1)
    cache.get_or_fetch("v1", fetch, force=True)
    assert stored[-1] == ("v1", 2, clock.wall)
