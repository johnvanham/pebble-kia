import threading
from datetime import datetime, timezone

import pytest

from app.cache import CommandThrottle, StatusCache
from app.models import VehicleStatus


def _status(soc: int = 50, charging: bool = False) -> VehicleStatus:
    return VehicleStatus(
        soc_pct=soc,
        range_km=200,
        is_charging=charging,
        plug="ac" if charging else "unplugged",
        doors_locked=True,
        outside_temp_c=18,
        odo_km=12345,
        aux_battery_pct=87,
        updated_at=datetime.now(timezone.utc),
    )


class FakeFetch:
    def __init__(self, charging: bool = False) -> None:
        self.calls: list[bool] = []
        self.charging = charging

    def __call__(self, force: bool) -> VehicleStatus:
        self.calls.append(force)
        return _status(soc=len(self.calls), charging=self.charging)


class GatedFetch:
    """A fetch with one gate per call, so a test can decide the order two
    concurrent fetches start and finish in.

    A wake takes half a minute upstream, which is long enough for
    anything else to arrive during it, so the interleavings are worth
    pinning rather than assuming.
    """

    MAX_CALLS = 6

    def __init__(self, charging: bool = False) -> None:
        self.calls: list[bool] = []
        self.charging = charging
        self.fail: Exception | None = None
        self.started = [threading.Event() for _ in range(self.MAX_CALLS)]
        self.gates = [threading.Event() for _ in range(self.MAX_CALLS)]
        self._lock = threading.Lock()

    def __call__(self, force: bool) -> VehicleStatus:
        with self._lock:
            index = len(self.calls)
            self.calls.append(force)
        self.started[index].set()
        assert self.gates[index].wait(timeout=5)
        if self.fail is not None:
            raise self.fail
        return _status(soc=index + 1, charging=self.charging)

    def open_all(self) -> None:
        for gate in self.gates:
            gate.set()


def _in_thread(fn):
    """Run fn in a thread and return (thread, results-list)."""
    out: list = []

    def run():
        try:
            out.append(fn())
        except BaseException as exc:  # noqa: BLE001 - the test inspects it
            out.append(exc)

    t = threading.Thread(target=run)
    t.start()
    return t, out


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


def test_bypass_fresh_refetches_inside_ttl(clock):
    cache = StatusCache(min_interval_seconds=600)
    fetch = FakeFetch()

    cache.get_or_fetch("v1", fetch, force=False)
    clock.advance(1)
    _, _, from_cache, forced = cache.get_or_fetch(
        "v1", fetch, force=False, bypass_fresh=True
    )

    assert (from_cache, forced) == (False, False)
    assert fetch.calls == [False, False]


def test_bypass_fresh_result_serves_later_ordinary_reads(clock):
    cache = StatusCache(min_interval_seconds=600)
    fetch = FakeFetch()

    status, _, _, _ = cache.get_or_fetch(
        "v1", fetch, force=False, bypass_fresh=True
    )
    clock.advance(60)
    again, _, from_cache, _ = cache.get_or_fetch("v1", fetch, force=False)

    assert from_cache is True
    assert again is status
    assert fetch.calls == [False]


def test_bypass_fresh_does_not_change_a_forced_read(clock):
    cache = StatusCache(min_interval_seconds=600)
    fetch = FakeFetch()

    cache.get_or_fetch("v1", fetch, force=False)
    clock.advance(1)
    _, _, from_cache, forced = cache.get_or_fetch(
        "v1", fetch, force=True, bypass_fresh=True
    )

    assert (from_cache, forced) == (False, True)
    assert fetch.calls == [False, True]


def test_force_bypasses_fresh_entry(clock):
    cache = StatusCache(min_interval_seconds=600)
    fetch = FakeFetch()

    cache.get_or_fetch("v1", fetch, force=False)
    clock.advance(1)
    _, _, from_cache, forced = cache.get_or_fetch("v1", fetch, force=True)

    assert (from_cache, forced) == (False, True)
    assert fetch.calls == [False, True]


def test_every_force_wakes(clock):
    # No floor: a pull on the watch always reaches the car.
    cache = StatusCache(min_interval_seconds=600)
    fetch = FakeFetch()

    for _ in range(3):
        _, _, from_cache, forced = cache.get_or_fetch("v1", fetch, force=True)
        assert (from_cache, forced) == (False, True)

    assert fetch.calls == [True, True, True]


def test_a_second_force_during_a_wake_shares_it(clock):
    cache = StatusCache(min_interval_seconds=600)
    fetch = GatedFetch()

    first, out1 = _in_thread(lambda: cache.get_or_fetch("v1", fetch, force=True))
    assert fetch.started[0].wait(timeout=5)
    second, out2 = _in_thread(lambda: cache.get_or_fetch("v1", fetch, force=True))
    second.join(timeout=0.2)
    # Still waiting on the first wake, not off starting its own.
    assert second.is_alive()

    fetch.open_all()
    first.join(timeout=5)
    second.join(timeout=5)

    assert fetch.calls == [True]
    status1, wall1, from_cache1, forced1 = out1[0]
    status2, wall2, from_cache2, forced2 = out2[0]
    assert status2 is status1
    assert wall2 == wall1
    assert (from_cache1, forced1) == (False, True)
    assert (from_cache2, forced2) == (False, True)


def test_an_ordinary_read_joins_an_in_flight_wake(clock):
    cache = StatusCache(min_interval_seconds=600)
    fetch = GatedFetch()

    first, _ = _in_thread(lambda: cache.get_or_fetch("v1", fetch, force=True))
    assert fetch.started[0].wait(timeout=5)
    second, out2 = _in_thread(lambda: cache.get_or_fetch("v1", fetch, force=False))
    second.join(timeout=0.2)
    assert second.is_alive()

    fetch.open_all()
    first.join(timeout=5)
    second.join(timeout=5)

    assert fetch.calls == [True]
    _, _, from_cache, forced = out2[0]
    # It got the wake's answer, and says so.
    assert (from_cache, forced) == (False, True)


def test_a_fresh_read_joins_an_in_flight_wake(clock):
    cache = StatusCache(min_interval_seconds=600)
    fetch = GatedFetch()

    first, _ = _in_thread(lambda: cache.get_or_fetch("v1", fetch, force=True))
    assert fetch.started[0].wait(timeout=5)
    second, out2 = _in_thread(
        lambda: cache.get_or_fetch("v1", fetch, force=False, bypass_fresh=True)
    )
    second.join(timeout=0.2)
    assert second.is_alive()

    fetch.open_all()
    first.join(timeout=5)
    second.join(timeout=5)

    assert fetch.calls == [True]
    assert out2[0][3] is True


def test_a_fresh_entry_still_serves_ordinary_reads_during_a_wake(clock):
    cache = StatusCache(min_interval_seconds=600)
    warm = FakeFetch()
    primed, _, _, _ = cache.get_or_fetch("v1", warm, force=False)

    fetch = GatedFetch()
    first, _ = _in_thread(lambda: cache.get_or_fetch("v1", fetch, force=True))
    assert fetch.started[0].wait(timeout=5)
    second, out2 = _in_thread(lambda: cache.get_or_fetch("v1", fetch, force=False))
    second.join(timeout=1)
    # Answered from the entry without waiting the wake out.
    assert not second.is_alive()
    status, _, from_cache, _ = out2[0]
    assert from_cache is True
    assert status is primed

    fetch.open_all()
    first.join(timeout=5)


def test_a_force_during_an_ordinary_fetch_starts_its_own(clock):
    cache = StatusCache(min_interval_seconds=600)
    fetch = GatedFetch()

    first, out1 = _in_thread(lambda: cache.get_or_fetch("v1", fetch, force=False))
    assert fetch.started[0].wait(timeout=5)
    second, out2 = _in_thread(lambda: cache.get_or_fetch("v1", fetch, force=True))
    assert fetch.started[1].wait(timeout=5)

    fetch.open_all()
    first.join(timeout=5)
    second.join(timeout=5)

    assert fetch.calls == [False, True]
    assert out1[0][3] is False
    assert out2[0][3] is True


def test_a_late_arrival_joins_the_wake_not_the_read_it_superseded(clock):
    # The live source serialises upstream calls, so a force arriving
    # during an ordinary read waits for it and finishes second. The
    # wake must still own the in-flight slot when the ordinary read
    # completes, or a third request starts a second wake.
    cache = StatusCache(min_interval_seconds=600)
    fetch = GatedFetch()

    ordinary, _ = _in_thread(lambda: cache.get_or_fetch("v1", fetch, force=False))
    assert fetch.started[0].wait(timeout=5)
    forced, out_f = _in_thread(lambda: cache.get_or_fetch("v1", fetch, force=True))
    assert fetch.started[1].wait(timeout=5)

    fetch.gates[0].set()
    ordinary.join(timeout=5)
    assert not ordinary.is_alive()

    joiner, out_j = _in_thread(lambda: cache.get_or_fetch("v1", fetch, force=True))
    joiner.join(timeout=0.2)
    assert joiner.is_alive()

    fetch.gates[1].set()
    forced.join(timeout=5)
    joiner.join(timeout=5)

    assert fetch.calls == [False, True]
    assert out_j[0][0] is out_f[0][0]
    assert out_j[0][3] is True


def test_a_wake_result_is_not_clobbered_by_an_older_read(clock):
    stored: list[int] = []
    cache = StatusCache(
        min_interval_seconds=600,
        on_store=lambda vid, st, wall: stored.append(st.soc_pct),
    )
    fetch = GatedFetch()

    ordinary, out_o = _in_thread(
        lambda: cache.get_or_fetch("v1", fetch, force=False)
    )
    assert fetch.started[0].wait(timeout=5)
    forced, out_f = _in_thread(lambda: cache.get_or_fetch("v1", fetch, force=True))
    assert fetch.started[1].wait(timeout=5)

    clock.advance(10)
    fetch.gates[1].set()
    forced.join(timeout=5)
    fetch.gates[0].set()
    ordinary.join(timeout=5)

    # Both callers get their own answer, but the cache and the store keep
    # the wake's, not whichever finished last.
    assert out_o[0][0] is not out_f[0][0]
    served, _, from_cache, _ = cache.get_or_fetch("v1", fetch, force=False)
    assert from_cache is True
    assert served is out_f[0][0]
    assert stored == [out_f[0][0].soc_pct]


def test_a_failed_fetch_is_shared_and_clears_the_slot(clock):
    cache = StatusCache(min_interval_seconds=600)
    fetch = GatedFetch()
    fetch.fail = RuntimeError("car unreachable")

    first, out1 = _in_thread(lambda: cache.get_or_fetch("v1", fetch, force=True))
    assert fetch.started[0].wait(timeout=5)
    second, out2 = _in_thread(lambda: cache.get_or_fetch("v1", fetch, force=True))
    second.join(timeout=0.2)
    assert second.is_alive()

    fetch.gates[0].set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert isinstance(out1[0], RuntimeError)
    assert isinstance(out2[0], RuntimeError)
    assert fetch.calls == [True]

    # The slot is free again, so the retry really retries.
    fetch.fail = None
    fetch.open_all()
    _, _, _, forced = cache.get_or_fetch("v1", fetch, force=True)
    assert forced is True
    assert fetch.calls == [True, True]


def test_coalescing_is_per_vehicle(clock):
    cache = StatusCache(min_interval_seconds=600)
    fetch = GatedFetch()

    first, _ = _in_thread(lambda: cache.get_or_fetch("v1", fetch, force=True))
    assert fetch.started[0].wait(timeout=5)
    second, _ = _in_thread(lambda: cache.get_or_fetch("v2", fetch, force=True))
    assert fetch.started[1].wait(timeout=5)

    fetch.open_all()
    first.join(timeout=5)
    second.join(timeout=5)
    assert fetch.calls == [True, True]


def test_charging_upgrades_a_poll_once_the_window_has_passed(clock):
    cache = StatusCache(min_interval_seconds=600, charging_refresh_seconds=60)
    fetch = FakeFetch(charging=True)

    cache.get_or_fetch("v1", fetch, force=True)
    clock.advance(61)
    _, _, from_cache, forced = cache.get_or_fetch("v1", fetch, force=False)

    assert (from_cache, forced) == (False, True)
    assert fetch.calls == [True, True]


def test_charging_upgrade_waits_out_its_window(clock):
    cache = StatusCache(min_interval_seconds=600, charging_refresh_seconds=60)
    fetch = FakeFetch(charging=True)

    cache.get_or_fetch("v1", fetch, force=True)
    clock.advance(30)
    _, _, from_cache, forced = cache.get_or_fetch("v1", fetch, force=False)

    assert (from_cache, forced) == (True, False)
    assert fetch.calls == [True]


def test_charging_upgrade_is_paced_from_the_last_wake_not_the_entry(clock):
    # The regression that started all this: an ordinary read refreshes
    # the entry, so pacing the upgrade on entry age let a short refresh
    # window starve it and the watch watched a charge through Kia's
    # copy, exactly what the upgrade exists to prevent.
    cache = StatusCache(min_interval_seconds=30, charging_refresh_seconds=60)
    fetch = FakeFetch(charging=True)

    cache.get_or_fetch("v1", fetch, force=True)

    # An ordinary read lands in between and stores a brand-new entry.
    clock.advance(30)
    _, _, _, forced = cache.get_or_fetch("v1", fetch, force=False)
    assert forced is False

    # A minute after the wake the next poll still upgrades, even though
    # the entry it would be served from is only thirty seconds old.
    clock.advance(30)
    _, _, _, forced = cache.get_or_fetch("v1", fetch, force=False)
    assert forced is True
    assert fetch.calls == [True, False, True]


def test_charging_upgrade_stops_once_charging_ends(clock):
    cache = StatusCache(min_interval_seconds=600, charging_refresh_seconds=60)
    fetch = FakeFetch(charging=True)

    cache.get_or_fetch("v1", fetch, force=True)
    clock.advance(61)
    fetch.charging = False
    _, _, _, forced = cache.get_or_fetch("v1", fetch, force=False)
    assert forced is True

    # The wake showed the session over, so polls are ordinary again.
    clock.advance(61)
    _, _, from_cache, forced = cache.get_or_fetch("v1", fetch, force=False)
    assert (from_cache, forced) == (True, False)
    assert fetch.calls == [True, True]


def test_charging_upgrade_is_off_at_zero(clock):
    cache = StatusCache(min_interval_seconds=600)
    fetch = FakeFetch(charging=True)

    cache.get_or_fetch("v1", fetch, force=False)
    clock.advance(300)
    _, _, from_cache, _ = cache.get_or_fetch("v1", fetch, force=False)

    assert from_cache is True
    assert fetch.calls == [False]


def test_a_charging_car_is_woken_on_the_first_poll_after_a_restart(clock):
    cache = StatusCache(min_interval_seconds=600, charging_refresh_seconds=60)
    fetch = FakeFetch(charging=True)

    cache.warm({"v1": (_status(charging=True), clock.wall - 120)})
    _, _, from_cache, forced = cache.get_or_fetch("v1", fetch, force=False)

    assert (from_cache, forced) == (False, True)
    assert fetch.calls == [True]


def test_a_fresh_read_never_wakes_a_charging_car(clock):
    # fresh=1 is what the companion sends after a remote command.
    # Stopping a charge must not be followed by waking the car to ask
    # about it, so the charging upgrade leaves these alone.
    cache = StatusCache(min_interval_seconds=600, charging_refresh_seconds=60)
    fetch = FakeFetch(charging=True)

    cache.get_or_fetch("v1", fetch, force=True)
    clock.advance(300)
    _, _, from_cache, forced = cache.get_or_fetch(
        "v1", fetch, force=False, bypass_fresh=True
    )

    assert (from_cache, forced) == (False, False)
    assert fetch.calls == [True, False]


def test_an_upgraded_poll_is_a_wake_that_others_join(clock):
    cache = StatusCache(min_interval_seconds=600, charging_refresh_seconds=60)
    warm = FakeFetch(charging=True)
    cache.get_or_fetch("v1", warm, force=False)
    clock.advance(61)

    fetch = GatedFetch(charging=True)
    first, out1 = _in_thread(lambda: cache.get_or_fetch("v1", fetch, force=False))
    assert fetch.started[0].wait(timeout=5)
    # The poll became a wake, so the entry it would otherwise have been
    # served from no longer answers for it.
    assert fetch.calls == [True]

    second, out2 = _in_thread(lambda: cache.get_or_fetch("v1", fetch, force=False))
    second.join(timeout=0.2)
    assert second.is_alive()

    fetch.open_all()
    first.join(timeout=5)
    second.join(timeout=5)

    assert fetch.calls == [True]
    assert out1[0][3] is True
    assert out2[0][3] is True


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


def test_command_throttle_allows_the_first_command(clock):
    throttle = CommandThrottle(min_interval_seconds=10)

    assert throttle.seconds_until_allowed("v1") == 0.0
    assert throttle.reserve("v1") == (0.0, None)


def test_command_throttle_blocks_inside_the_window(clock):
    throttle = CommandThrottle(min_interval_seconds=10)

    throttle.reserve("v1")
    clock.advance(4)

    assert throttle.seconds_until_allowed("v1") == pytest.approx(6.0)
    wait, previous = throttle.reserve("v1")
    assert wait == pytest.approx(6.0)
    assert previous is None


def test_command_throttle_expires(clock):
    throttle = CommandThrottle(min_interval_seconds=10)

    throttle.reserve("v1")
    clock.advance(10)

    assert throttle.seconds_until_allowed("v1") == 0.0


def test_command_throttle_is_per_vehicle(clock):
    throttle = CommandThrottle(min_interval_seconds=10)

    throttle.reserve("v1")

    assert throttle.seconds_until_allowed("v2") == 0.0


def test_a_reserved_slot_blocks_a_second_command_before_the_first_returns(clock):
    # The send can sit behind a wake for half a minute. Reserving up
    # front is what keeps every command that arrives meanwhile from
    # passing the gate too.
    throttle = CommandThrottle(min_interval_seconds=10)

    wait, _ = throttle.reserve("v1")
    assert wait == 0.0
    wait, _ = throttle.reserve("v1")
    assert wait == pytest.approx(10.0)


def test_restoring_a_slot_lets_the_retry_through(clock):
    throttle = CommandThrottle(min_interval_seconds=10)

    _, previous = throttle.reserve("v1")
    throttle.restore("v1", previous)

    assert throttle.reserve("v1") == (0.0, None)


def test_restoring_a_slot_keeps_the_earlier_window(clock):
    throttle = CommandThrottle(min_interval_seconds=10)

    throttle.reserve("v1")
    clock.advance(10)
    _, previous = throttle.reserve("v1")
    clock.advance(1)
    throttle.restore("v1", previous)

    # The failed command's slot went back to the one before it, which
    # is 11s old, so the window has elapsed.
    assert throttle.seconds_until_allowed("v1") == 0.0


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
