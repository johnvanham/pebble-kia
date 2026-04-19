"""Push-notification transports.

The proxy's transition detector calls `notifier.push(...)` on every
state change worth surfacing. The concrete notifier is chosen at
startup: NtfyNotifier for real self-hosted (or public) ntfy, or
NullNotifier when NTFY_URL is unset (local dev, tests).
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

log = logging.getLogger("notifier")


class Notifier(Protocol):
    async def push(
        self, title: str, body: str, tags: list[str] | None = None
    ) -> None: ...

    async def close(self) -> None: ...


class NullNotifier:
    """No-op sink. Transitions are still logged so you can see what would fire."""

    async def push(self, title, body, tags=None):
        log.info("would notify: %s — %s", title, body)

    async def close(self):
        pass


class NtfyNotifier:
    def __init__(
        self, base_url: str, topic: str, auth_token: str | None = None
    ) -> None:
        self.url = f"{base_url.rstrip('/')}/{topic}"
        self.auth_token = auth_token
        self._client = httpx.AsyncClient(timeout=10.0)

    async def push(self, title, body, tags=None):
        headers = {"Title": title}
        if tags:
            headers["Tags"] = ",".join(tags)
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        try:
            r = await self._client.post(
                self.url,
                content=(body or "").encode("utf-8"),
                headers=headers,
            )
            r.raise_for_status()
            log.info("notified: %s — %s", title, body)
        except httpx.HTTPError as e:
            # ntfy is best-effort; log and carry on so a flaky push doesn't
            # wedge the transition detector loop.
            log.warning("ntfy push failed: %s", e)

    async def close(self):
        await self._client.aclose()
