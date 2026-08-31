import threading
import time
from dataclasses import dataclass
from typing import Callable

from .models import VehicleStatus


@dataclass
class CacheEntry:
    status: VehicleStatus
    fetched_at: float  # monotonic() seconds since process start
    wall_fetched_at: float  # time.time() seconds since epoch


class StatusCache:
    def __init__(
        self,
        min_interval_seconds: int,
        force_min_interval_seconds: int = 0,
        on_store: Callable[[str, VehicleStatus, float], None] | None = None,
    ) -> None:
        self.min_interval = min_interval_seconds
        self.force_min_interval = force_min_interval_seconds
        self._on_store = on_store
        self._entries: dict[str, CacheEntry] = {}
        self._last_forced_at: dict[str, float] = {}
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
                # fresh. Warmed entries are deliberately not recorded as
                # forced: a restart shouldn't block the first wake.
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
    ) -> tuple[VehicleStatus, float, bool, bool]:
        """Return (status, wall_fetched_at_epoch, from_cache, forced).

        `fetch` is called with the *effective* force, which the source
        uses to decide between waking the vehicle and reading the
        upstream's own cache. A force=True request arriving within
        force_min_interval_seconds of the last forced fetch is
        downgraded rather than refused: it serves a fresh cache entry if
        there is one, otherwise fetches unforced. `forced` reports what
        actually happened so the caller can tell a wake from a downgrade.
        """
        now_mono = time.monotonic()
        with self._lock:
            entry = self._entries.get(vehicle_id)
            fresh = (
                entry is not None
                and (now_mono - entry.fetched_at) < self.min_interval
            )
            if force:
                last_forced = self._last_forced_at.get(vehicle_id)
                force = (
                    last_forced is None
                    or (now_mono - last_forced) >= self.force_min_interval
                )
            if not force and fresh:
                assert entry is not None
                return entry.status, entry.wall_fetched_at, True, False

        # Call fetch() outside the lock — it can take a while (network).
        status = fetch(force)
        wall_now = time.time()
        with self._lock:
            self._entries[vehicle_id] = CacheEntry(
                status=status,
                fetched_at=time.monotonic(),
                wall_fetched_at=wall_now,
            )
            if force:
                # Stamped on success only, so a failed wake doesn't lock
                # out the retry for the whole force window.
                self._last_forced_at[vehicle_id] = time.monotonic()
        if self._on_store is not None:
            self._on_store(vehicle_id, status, wall_now)
        return status, wall_now, False, force
