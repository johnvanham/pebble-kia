"""QR images for phone setup, written at startup.

Configuring the watchapp means getting a base URL and a 64-hex bearer
token into Clay's settings page. Typing the token on a phone keyboard is
miserable, and scanning it inside the settings page is impossible:
pebble-clay serves that page from a `data:` URI, which is an opaque
origin and therefore not a secure context, so `getUserMedia` is
unavailable there. Instead the proxy writes the QR codes on the machine
it runs on; the owner opens them, scans with the phone's ordinary camera
app, and pastes.

The token image is a live credential in visual form. It is written
owner-only and is deliberately never exposed over HTTP — no route, no
StaticFiles mount — because anything reachable from the LAN would hand
the token to anyone who asks. The same is true of the optional log
block: see `_log_terminal_qr`.
"""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path

import segno

from .config import Settings

log = logging.getLogger(__name__)

# 8 px per module puts a 64-hex token at 360x360 — big enough for a
# phone to lock onto across a desk.
_SCALE = 8


def _encode(payload: str) -> segno.QRCode:
    # make_qr, not make: for a short payload segno would otherwise pick a
    # Micro QR, which phone camera apps (and zbar) do not decode.
    return segno.make_qr(payload, error="m")


def _write_png(path: Path, payload: str) -> None:
    # Remove first, and clear up again if anything goes wrong, so the
    # file is either the current token or absent. Rewriting in place —
    # atomically or not — leaves the previous image behind when the write
    # fails, and an old QR still scans, so it passes for current.
    path.unlink(missing_ok=True)
    # O_CREAT's mode is filtered by the umask, so fchmod after the fact
    # is what actually guarantees owner-only.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), 0o600)
            _encode(payload).save(f, kind="png", scale=_SCALE)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _log_terminal_qr(payload: str) -> None:
    # This block is the bearer token, in a form any camera decodes. Logs
    # are retained past token rotation, shipped off the host by
    # collectors, and pasted into issues, so it is opt-in (SETUP_QR_LOG)
    # rather than on: it is a weaker place to keep the credential than
    # the 0600 file. It exists because on a headless Docker host the log
    # is the only channel out.
    buf = io.StringIO()
    _encode(payload).terminal(out=buf, compact=True)
    log.info("bearer token QR (scan with the phone camera):\n%s", buf.getvalue())


def emit_setup_qr(settings: Settings) -> None:
    if settings.setup_qr_dir is None:
        log.info("setup QR disabled (SETUP_QR_DIR empty)")
        return

    directory = settings.setup_qr_dir
    token_png = directory / "bearer-token.png"
    # Drop the previous image before anything that can fail, not just
    # before the write: mkdir and chmod can raise on a read-only mount or
    # a directory this process may not chmod, and a surviving old QR
    # still scans, so it would pass for current.
    try:
        token_png.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)

        _write_png(directory / "bearer-token.png", settings.bearer_token)
        if settings.setup_qr_log:
            _log_terminal_qr(settings.bearer_token)

        if settings.proxy_public_url:
            _write_png(directory / "proxy-url.png", settings.proxy_public_url)
            log.info("setup QR written to %s (bearer-token.png, proxy-url.png)",
                     directory)
        else:
            log.info("setup QR written to %s (bearer-token.png); set "
                     "PROXY_PUBLIC_URL to also get proxy-url.png",
                     directory)
    except Exception:
        # A missing setup QR is an inconvenience; a proxy that won't boot
        # is an outage.
        log.warning("could not write setup QR to %s", directory, exc_info=True)
