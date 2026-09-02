import logging
import math
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from hyundai_kia_connect_api.exceptions import (
    AuthenticationError,
    AuthenticationOTPRequired,
    ConsentRequiredError,
    DuplicateRequestError,
    HyundaiKiaException,
    NoDataFound,
    RateLimitingError,
    RequestTimeoutError,
    ServiceTemporaryUnavailable,
)

from .auth import verify_bearer
from .cache import CommandThrottle, StatusCache
from .config import Settings, configure_logging, load_settings
from .models import ActionResponse, StatusResponse, VehicleList, VehicleStatus
from .sources.base import ACTION_PARAMS, ACTIONS, DataSource, VehicleNotFound
from .sources.demo import DemoDataSource
from .sources.live import LiveDataSource
from .setup_qr import emit_setup_qr
from .store import StateStore

log = logging.getLogger(__name__)


def _build_source(settings: Settings, store: StateStore) -> DataSource:
    if settings.data_source == "demo":
        return DemoDataSource(settings.demo_data_file)
    if settings.data_source == "live":
        return LiveDataSource(settings, store)
    raise RuntimeError(f"unknown DATA_SOURCE: {settings.data_source}")


def _build_cache(settings: Settings, store: StateStore) -> StatusCache:
    live = settings.data_source == "live"
    return StatusCache(
        settings.live_refresh_min_seconds if live else settings.demo_refresh_min_seconds,
        # Only live has a car to wake — the demo's short TTL already
        # keeps a charging scenario moving.
        charging_refresh_seconds=settings.live_charging_refresh_seconds if live else 0,
        on_store=store.save_status,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    app.state.settings = settings
    # uvicorn only configures its own loggers, so without this the app's
    # own info-level output (setup QR, missing-field warnings) is dropped.
    configure_logging(settings)
    emit_setup_qr(settings)
    app.state.store = store = StateStore(settings.state_db)
    app.state.source = _build_source(settings, store)

    app.state.cache = cache = _build_cache(settings, store)
    cache.warm(store.load_statuses())
    app.state.command_throttle = CommandThrottle(settings.command_min_seconds)
    yield


app = FastAPI(title="pebble-kia-proxy", version="0.1.0", lifespan=lifespan)


# Kia's failures are the ones a user can act on, so they get distinct
# statuses the companion turns into readable strings rather than a
# blanket 500.
_KIA_STATUS: list[tuple[type[Exception], int, str]] = [
    (ConsentRequiredError, 503, "Kia needs consent - open the Kia app and accept"),
    (AuthenticationOTPRequired, 503, "Kia login needs a one-time password"),
    (AuthenticationError, 502, "Kia login failed - check KIA_USERNAME / KIA_PASSWORD"),
    (RateLimitingError, 429, "Kia is rate limiting this account"),
    (NoDataFound, 404, "Kia has no data for this vehicle"),
    (DuplicateRequestError, 503, "Kia is still processing the previous request"),
    (RequestTimeoutError, 503, "Kia timed out"),
    (ServiceTemporaryUnavailable, 503, "Kia is temporarily unavailable"),
]


@app.exception_handler(HyundaiKiaException)
async def _kia_error_handler(_req, exc: HyundaiKiaException):
    for exc_type, code, detail in _KIA_STATUS:
        if isinstance(exc, exc_type):
            return JSONResponse(status_code=code, content={"detail": detail})
    return JSONResponse(status_code=502, content={"detail": f"Kia error: {exc}"})


@app.exception_handler(FileNotFoundError)
async def _demo_missing_handler(_req, exc: FileNotFoundError):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": f"demo data file not found: {exc.filename}"},
    )


@app.get("/health")
def health():
    return {"status": "ok", "data_source": app.state.source.name}


@app.get("/vehicles",
         response_model=VehicleList,
         dependencies=[Depends(verify_bearer)])
def list_vehicles():
    return VehicleList(vehicles=app.state.source.list_vehicles())


@app.get("/vehicles/{vehicle_id}/status",
         response_model=StatusResponse,
         dependencies=[Depends(verify_bearer)])
def get_status(vehicle_id: str, force: int = 0, fresh: int = 0):
    return _status(vehicle_id, bool(force), fresh=bool(fresh))


@app.post("/vehicles/{vehicle_id}/refresh",
          response_model=StatusResponse,
          dependencies=[Depends(verify_bearer)])
def refresh_status(vehicle_id: str):
    return _status(vehicle_id, True)


# Remote commands mutate the vehicle, so they are opt-in
# (ENABLE_COMMANDS) and endpoint-only: nothing in this codebase may
# fire an action except this handler serving an explicit client
# request. No timer, ever.
@app.post("/vehicles/{vehicle_id}/actions/{action}",
          response_model=ActionResponse,
          dependencies=[Depends(verify_bearer)])
def perform_action(vehicle_id: str, action: str,
                   ac: int | None = None, dc: int | None = None):
    settings: Settings = app.state.settings
    if not settings.enable_commands:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Commands disabled - set ENABLE_COMMANDS=1 on the proxy",
        )
    if action not in ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown action: {action} (valid: {', '.join(ACTIONS)})",
        )
    params = _action_params(action, {"ac": ac, "dc": dc})
    throttle: CommandThrottle = app.state.command_throttle
    # Reserved before the send, not stamped after it: perform_action can
    # sit behind a wake already in progress for tens of seconds, and a
    # gate that only stamps on the way out would let every command that
    # arrives meanwhile through.
    wait, previous = throttle.reserve(vehicle_id)
    if wait > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"too soon after the last command - retry in {math.ceil(wait)}s",
        )
    try:
        app.state.source.perform_action(vehicle_id, action, params)
    except VehicleNotFound:
        throttle.restore(vehicle_id, previous)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"vehicle not found: {vehicle_id}",
        )
    except Exception:
        # A send Kia rejected must not lock out the retry.
        throttle.restore(vehicle_id, previous)
        raise
    return ActionResponse(id=vehicle_id, action=action)


# Charge targets are a percentage the car offers in tenths, so anything
# else is a client bug rather than something to round into shape: a
# silently corrected 85 would leave the watch showing a limit the car
# never got.
def _action_params(action: str, given: dict[str, int | None]) -> dict[str, int] | None:
    expected = ACTION_PARAMS.get(action, ())
    for name, value in given.items():
        if value is not None and name not in expected:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{action} takes no {name} parameter",
            )
    if not expected:
        return None
    params: dict[str, int] = {}
    for name in expected:
        value = given.get(name)
        if value is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{action} needs {' and '.join(expected)}",
            )
        if not 10 <= value <= 100 or value % 10:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{name} must be a multiple of 10 between 10 and 100",
            )
        params[name] = value
    return params


def _status(vehicle_id: str, force: bool, fresh: bool = False) -> StatusResponse:
    source: DataSource = app.state.source
    cache: StatusCache = app.state.cache

    def do_fetch(effective_force: bool) -> VehicleStatus:
        try:
            return source.fetch_status(vehicle_id, force=effective_force)
        except VehicleNotFound:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"vehicle not found: {vehicle_id}",
            )

    # force wins over fresh: a wake is already the freshest read there is.
    status_obj, wall_fetched, from_cache, forced = cache.get_or_fetch(
        vehicle_id, do_fetch, force=force, bypass_fresh=fresh
    )
    return StatusResponse(
        id=vehicle_id,
        status=status_obj,
        fetched_at=datetime.fromtimestamp(wall_fetched, tz=timezone.utc),
        from_cache=from_cache,
        forced=forced,
    )
