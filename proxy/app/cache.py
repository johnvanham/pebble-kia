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
    def __init__(self, min_interval_seconds: int) -> None:
        self.min_interval = min_interval_seconds
        self._entries: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()

    def get_or_fetch(
        self,
        vehicle_id: str,
        fetch: Callable[[], VehicleStatus],
        force: bool,
    ) -> tuple[VehicleStatus, float, bool]:
        """Return (status, wall_fetched_at_epoch, from_cache).

        When force=False and a cached entry is younger than min_interval,
        the cached entry is returned. Otherwise fetch() is called and its
        result replaces the cache entry.
        """
        now_mono = time.monotonic()
        with self._lock:
            entry = self._entries.get(vehicle_id)
            fresh = (
                entry is not None
                and (now_mono - entry.fetched_at) < self.min_interval
            )
            if not force and fresh:
                assert entry is not None
                return entry.status, entry.wall_fetched_at, True

        # Call fetch() outside the lock — it can take a while (network).
        status = fetch()
        wall_now = time.time()
        with self._lock:
            self._entries[vehicle_id] = CacheEntry(
                status=status,
                fetched_at=time.monotonic(),
                wall_fetched_at=wall_now,
            )
        return status, wall_now, False
