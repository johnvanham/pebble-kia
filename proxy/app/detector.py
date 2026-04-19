"""Background state-change detector.

Polls each vehicle's status on a fixed interval, diffs against the last
observation, and fires a push via the configured notifier on meaningful
transitions (charge start/end, plug/unplug, lock/unlock, climate
on/off). The detector is authoritative: the companion used to do this
client-side, but moving it here means notifications fire whether or
not the watchapp is open, and the phone + watch both see a standard
OS notification via the ntfy app + Pebble's notification-bridging.
"""

from __future__ import annotations

import asyncio
import logging

from .models import VehicleStatus
from .notifier import Notifier
from .sources.base import DataSource, VehicleNotFound

log = logging.getLogger("detector")

_PLUG_LABELS = {"unplugged": "unplugged", "ac": "AC", "dc": "DC"}


def _status_transitions(
    name: str, prev: VehicleStatus, cur: VehicleStatus
) -> list[tuple[str, str, list[str]]]:
    """Return a list of (title, body, tags) pushes implied by prev→cur."""
    out: list[tuple[str, str, list[str]]] = []

    if not prev.is_charging and cur.is_charging:
        body = f"{cur.charge_kw:.1f} kW"
        if cur.charge_eta_min > 0:
            body += f" • ETA {cur.charge_eta_min} min"
        out.append((f"{name}: Charging", body, ["battery_charging"]))
    elif prev.is_charging and not cur.is_charging:
        done = "Charge complete" if cur.soc_pct >= 80 else "Charging stopped"
        out.append((
            f"{name}: {done}",
            f"{cur.soc_pct}% • {cur.range_km} km",
            ["battery_charging"],
        ))

    if prev.plug != cur.plug:
        if prev.plug == "unplugged" and cur.plug != "unplugged":
            out.append((
                f"{name}: Plugged in",
                _PLUG_LABELS[cur.plug],
                ["electric_plug"],
            ))
        elif prev.plug != "unplugged" and cur.plug == "unplugged":
            out.append((
                f"{name}: Unplugged",
                f"{cur.soc_pct}% • {cur.range_km} km",
                ["electric_plug"],
            ))

    if prev.doors_locked != cur.doors_locked:
        if cur.doors_locked:
            out.append((f"{name}: Locked", "", ["lock"]))
        else:
            out.append((f"{name}: Unlocked", "", ["unlock"]))

    if prev.is_climate_on != cur.is_climate_on:
        if cur.is_climate_on:
            out.append((
                f"{name}: Climate on",
                f"{cur.cabin_temp_c}°C cabin",
                ["fire"],
            ))
        else:
            out.append((f"{name}: Climate off", "", ["snowflake"]))

    return out


class TransitionDetector:
    def __init__(
        self,
        source: DataSource,
        notifier: Notifier,
        vehicle_nicknames: dict[str, str],
        interval_seconds: int,
    ) -> None:
        self.source = source
        self.notifier = notifier
        self.nicknames = vehicle_nicknames
        self.interval = interval_seconds
        self._prev: dict[str, VehicleStatus] = {}
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="transition-detector")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception:  # pragma: no cover
                log.exception("detector tick failed")
            await asyncio.sleep(self.interval)

    async def _tick(self) -> None:
        for vehicle_id, name in self.nicknames.items():
            try:
                # fetch_status is synchronous today (file read for demo,
                # will be network I/O for live). Offload to a thread so
                # the detector loop stays responsive.
                cur = await asyncio.to_thread(self.source.fetch_status, vehicle_id)
            except VehicleNotFound:
                continue
            prev = self._prev.get(vehicle_id)
            self._prev[vehicle_id] = cur
            if prev is None:
                # First observation establishes the baseline silently —
                # no "charging started" spam at process boot.
                continue
            for title, body, tags in _status_transitions(name, prev, cur):
                await self.notifier.push(title, body, tags)
