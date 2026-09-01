"""SQLite persistence for the Kia refresh token and last-known status.

Restarts are cheap for the proxy but expensive for the car: without
this, every boot re-logs-in and the first watch request pays a cold
fetch that wakes the vehicle. Both are avoidable.

A connection is opened per call. Traffic is a handful of writes an hour,
and FastAPI runs the sync route handlers in a threadpool, so per-call
connections sidestep sqlite3's thread affinity entirely.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path

from .models import VehicleStatus

log = logging.getLogger(__name__)

TOKEN_KEY = "kia_token"

# The library's Token dataclass carries the plaintext Kia password and
# PIN. Neither is needed to reuse a token — both are re-injected from
# settings at login time — so they never go to disk.
TOKEN_SECRET_KEYS = ("password", "pin")


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS vehicle_status ("
                "id TEXT PRIMARY KEY, status_json TEXT, wall_fetched_at REAL)"
            )
            conn.commit()

    def _connect(self):
        return closing(sqlite3.connect(self.path))

    def load_token(self) -> dict | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT value FROM kv WHERE key = ?", (TOKEN_KEY,)
                ).fetchone()
            if row is None:
                return None
            return json.loads(row[0])
        except (sqlite3.Error, json.JSONDecodeError, TypeError) as exc:
            # A half-written row or an older schema costs one fresh login,
            # which is recoverable. Failing startup is not.
            log.warning("discarding stored token: %s", exc)
            return None

    def save_token(self, token: dict) -> None:
        clean = {k: v for k, v in token.items() if k not in TOKEN_SECRET_KEYS}
        # default=str so a datetime expiry serialises; it reads back as
        # an ISO string, not a datetime.
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (TOKEN_KEY, json.dumps(clean, default=str)),
            )
            conn.commit()

    def load_statuses(self) -> dict[str, tuple[VehicleStatus, float]]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, status_json, wall_fetched_at FROM vehicle_status"
                ).fetchall()
        except sqlite3.Error as exc:
            log.warning("discarding stored statuses: %s", exc)
            return {}

        out: dict[str, tuple[VehicleStatus, float]] = {}
        for vehicle_id, status_json, wall_fetched_at in rows:
            try:
                status = VehicleStatus.model_validate_json(status_json)
            except Exception as exc:
                log.warning("discarding stored status for %s: %s", vehicle_id, exc)
                continue
            out[vehicle_id] = (status, wall_fetched_at)
        return out

    def save_status(
        self, vehicle_id: str, status: VehicleStatus, wall_fetched_at: float
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO vehicle_status (id, status_json, wall_fetched_at) "
                "VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "status_json = excluded.status_json, "
                "wall_fetched_at = excluded.wall_fetched_at",
                (vehicle_id, status.model_dump_json(), wall_fetched_at),
            )
            conn.commit()
