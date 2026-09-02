import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .models import VehicleStatus


@dataclass
class CacheEntry:
    status: VehicleStatus
    fetched_at: float  # monotonic() seconds since process start
    wall_fetched_at: float  # time.time() seconds since epoch


@dataclass
class _Inflight:
    """One upstream fetch in progress, shared by everyone who arrives
    while it runs. A forced read holds the car's attention for tens of
    seconds, and two pulls in that window want the same answer, not two
    wakes."""

    forced: bool
    done: threading.Event = field(default_factory=threading.Event)
    result: tuple[VehicleStatus, float] | None = None
    error: BaseException | None = None


class CommandThrottle:
    """Floor between remote commands, per vehicle.

    `reserve` stamps before the send rather than after it, because the
    send can block for the length of a wake already in progress and a
    check-then-act gate would let every command that arrives meanwhile
    through. A failed send hands the previous stamp back, so it doesn't
    lock out the retry.
    """

    def __init__(self, min_interval_seconds: int) -> None:
        self.min_interval = min_interval_seconds
        self._last_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def seconds_until_allowed(self, vehicle_id: str) -> float:
        with self._lock:
            return self._wait_locked(vehicle_id)

    def reserve(self, vehicle_id: str) -> tuple[float, float | None]:
        """Claim the next command slot for a vehicle.

        Returns (wait_seconds, previous_stamp). A non-zero wait means the
        slot was refused and nothing was stamped. Otherwise the slot is
        held from now, and `restore` puts the previous stamp back if the
        command turns out not to have been sent.
        """
        with self._lock:
            wait = self._wait_locked(vehicle_id)
            if wait > 0:
                return wait, None
            previous = self._last_at.get(vehicle_id)
            self._last_at[vehicle_id] = time.monotonic()
            return 0.0, previous

    def restore(self, vehicle_id: str, previous: float | None) -> None:
        with self._lock:
            if previous is None:
                self._last_at.pop(vehicle_id, None)
            else:
                self._last_at[vehicle_id] = previous

    def _wait_locked(self, vehicle_id: str) -> float:
        last = self._last_at.get(vehicle_id)
        if last is None:
            return 0.0
        return max(0.0, self.min_interval - (time.monotonic() - last))


class StatusCache:
    def __init__(
        self,
        min_interval_seconds: int,
        charging_refresh_seconds: int = 0,
        on_store: Callable[[str, VehicleStatus, float], None] | None = None,
    ) -> None:
        self.min_interval = min_interval_seconds
        # While the last-known state says the car is charging, an
        # ordinary read arriving this long after the last wake wakes the
        # car again instead of reading Kia's copy, so a client that
        # simply polls sees the charge progress. 0 disables the upgrade.
        self.charging_refresh = charging_refresh_seconds
        self._on_store = on_store
        self._entries: dict[str, CacheEntry] = {}
        self._last_forced_at: dict[str, float] = {}
        self._inflight: dict[str, _Inflight] = {}
        self._lock = threading.Lock()

    def warm(self, entries: dict[str, tuple[VehicleStatus, float]]) -> None:
        """Seed the cache from persisted {id: (status, wall_fetched_at)}."""
        now_mono = time.monotonic()
        now_wall = time.time()
        with self._lock:
            for vehicle_id, (status, wall_fetched_at) in entries.items():
                # Age has to survive the restart, so the monotonic stamp is
                # backdated by however old the persisted entry really is.
                # Warming with `now` would hand out a day-old status as
                # fresh.
                self._entries[vehicle_id] = CacheEntry(
                    status=status,
                    fetched_at=now_mono - (now_wall - wall_fetched_at),
                    wall_fetched_at=wall_fetched_at,
                )

    def get_or_fetch(
        self,
        vehicle_id: str,
        fetch: Callable[[bool], VehicleStatus],
        force: bool,
        *,
        bypass_fresh: bool = False,
    ) -> tuple[VehicleStatus, float, bool, bool]:
        """Return (status, wall_fetched_at_epoch, from_cache, forced).

        `fetch` is called with the *effective* force, which the source
        uses to decide between waking the vehicle and reading the
        upstream's own copy. A forced read always wakes the car; there
        is no floor. What bounds wakes is coalescing: a request that
        arrives while a fetch for the same vehicle is running waits for
        that fetch and shares its result, provided the running one is
        at least as fresh a read as the request asked for. `forced`
        reports what actually happened, including an ordinary read that
        was upgraded because the car was charging.

        `bypass_fresh` makes an ordinary read skip the fresh-entry
        shortcut and fetch unforced anyway. It never wakes the car —
        the charging upgrade leaves it alone — so a client can ask for
        the newest state Kia holds without paying for a wake.
        """
        now_mono = time.monotonic()
        with self._lock:
            entry = self._entries.get(vehicle_id)
            age = None if entry is None else now_mono - entry.fetched_at
            if not force and not bypass_fresh:
                force = self._charging_wake_due(vehicle_id, entry, now_mono)
            if (
                not force
                and age is not None
                and age < self.min_interval
                and not bypass_fresh
            ):
                assert entry is not None
                return entry.status, entry.wall_fetched_at, True, False
            running = self._inflight.get(vehicle_id)
            if running is not None and (running.forced or not force):
                mine = None
            else:
                # A force arriving during an ordinary fetch starts its
                # own and takes the slot, so later arrivals join the
                # wake rather than the cheaper read it supersedes.
                mine = running = _Inflight(forced=force)
                self._inflight[vehicle_id] = mine

        if mine is None:
            running.done.wait()
            if running.error is not None:
                raise running.error
            assert running.result is not None
            status, wall = running.result
            return status, wall, False, running.forced

        # fetch() runs outside the lock — it can take a while (network,
        # and a wake waits for the car to report).
        started = time.monotonic()
        try:
            status = fetch(force)
        except BaseException as exc:
            mine.error = exc
            self._finish(vehicle_id, mine)
            raise
        wall_now = time.time()
        mine.result = (status, wall_now)
        with self._lock:
            existing = self._entries.get(vehicle_id)
            # A read that began before a newer result landed must not
            # replace it: an ordinary read and a wake can be in flight
            # together, and the wake's answer is the better one however
            # the finishing order falls out.
            stored = existing is None or existing.fetched_at <= started
            if stored:
                self._entries[vehicle_id] = CacheEntry(
                    status=status,
                    fetched_at=time.monotonic(),
                    wall_fetched_at=wall_now,
                )
            if force:
                # Stamped whether or not the entry was kept: the car was
                # woken either way, and that is what the charging
                # upgrade paces itself against.
                self._last_forced_at[vehicle_id] = time.monotonic()
        self._finish(vehicle_id, mine)
        if stored and self._on_store is not None:
            self._on_store(vehicle_id, status, wall_now)
        return status, wall_now, False, force

    def _charging_wake_due(
        self, vehicle_id: str, entry: CacheEntry | None, now_mono: float
    ) -> bool:
        """Whether an ordinary read should wake a charging car instead.

        Paced from the last wake rather than from the cache entry's age,
        so an ordinary read landing in between (or a short
        LIVE_REFRESH_MIN_SECONDS) cannot reset the clock and starve the
        upgrade — which would leave the watch watching a charge through
        Kia's stale copy, the very thing this exists to fix.
        """
        if entry is None or not entry.status.is_charging:
            return False
        if not self.charging_refresh:
            return False
        last_forced = self._last_forced_at.get(vehicle_id)
        return (
            last_forced is None
            or (now_mono - last_forced) >= self.charging_refresh
        )

    def _finish(self, vehicle_id: str, mine: _Inflight) -> None:
        with self._lock:
            if self._inflight.get(vehicle_id) is mine:
                del self._inflight[vehicle_id]
        mine.done.set()
