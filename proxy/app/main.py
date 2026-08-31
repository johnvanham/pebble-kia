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
from .cache import StatusCache
from .config import Settings, detector_interval, load_settings
from .detector import TransitionDetector
from .models import StatusResponse, VehicleList, VehicleStatus
from .notifier import Notifier, NtfyNotifier, NullNotifier
from .sources.base import DataSource, VehicleNotFound
from .sources.demo import DemoDataSource
from .sources.live import LiveDataSource
from .store import StateStore


def _build_source(settings: Settings, store: StateStore) -> DataSource:
    if settings.data_source == "demo":
        return DemoDataSource(settings.demo_data_file)
    if settings.data_source == "live":
        return LiveDataSource(settings, store)
    raise RuntimeError(f"unknown DATA_SOURCE: {settings.data_source}")


def _build_notifier(settings: Settings) -> Notifier:
    if settings.ntfy_url and settings.ntfy_topic:
        return NtfyNotifier(
            settings.ntfy_url,
            settings.ntfy_topic,
            auth_token=settings.ntfy_auth_token or None,
        )
    return NullNotifier()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    app.state.settings = settings
    app.state.store = store = StateStore(settings.state_db)
    app.state.source = source = _build_source(settings, store)

    live = settings.data_source == "live"
    app.state.cache = cache = StatusCache(
        settings.live_refresh_min_seconds if live else settings.demo_refresh_min_seconds,
        # Only live has a car to wake — on demo a forced refresh is free.
        force_min_interval_seconds=settings.live_force_min_seconds if live else 0,
        on_store=store.save_status,
    )
    cache.warm(store.load_statuses())
    app.state.notifier = _build_notifier(settings)

    # Kick off the transition detector against whatever vehicles the
    # source currently knows about. If list_vehicles() raises (e.g. Kia
    # is unreachable at boot), we skip detection rather than refuse to
    # start — the HTTP API still serves clients.
    detector: TransitionDetector | None = None
    try:
        vehicles = source.list_vehicles()
        nicknames = {v.id: v.nickname for v in vehicles}
        detector = TransitionDetector(
            fetch_status=_cached_status,
            notifier=app.state.notifier,
            vehicle_nicknames=nicknames,
            interval_seconds=detector_interval(settings),
        )
        detector.start()
    except Exception:
        import logging
        logging.getLogger("lifespan").warning(
            "transition detector not started (source.list_vehicles failed)",
            exc_info=True,
        )
    app.state.detector = detector

    try:
        yield
    finally:
        if detector is not None:
            await detector.stop()
        await app.state.notifier.close()


app = FastAPI(title="pebble-kia-proxy", version="0.1.0", lifespan=lifespan)


# Kia's failures are the ones a user can act on, so they get distinct
# statuses the companion turns into readable strings rather than a
# blanket 500.
_KIA_STATUS: list[tuple[type[Exception], int, str]] = [
    (ConsentRequiredError, 503, "Kia needs consent — open the Kia app and accept"),
    (AuthenticationOTPRequired, 503, "Kia login needs a one-time password"),
    (AuthenticationError, 502, "Kia login failed — check KIA_USERNAME / KIA_PASSWORD"),
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
def get_status(vehicle_id: str, force: int = 0):
    return _status(vehicle_id, bool(force))


@app.post("/vehicles/{vehicle_id}/refresh",
          response_model=StatusResponse,
          dependencies=[Depends(verify_bearer)])
def refresh_status(vehicle_id: str):
    return _status(vehicle_id, True)


def _cached_status(vehicle_id: str) -> VehicleStatus:
    """Non-forced read for the detector, sharing the cache with clients.

    Going through the cache is what keeps one detector interval to one
    upstream call per vehicle instead of one per poller.
    """
    source: DataSource = app.state.source
    status_obj, _, _, _ = app.state.cache.get_or_fetch(
        vehicle_id,
        lambda effective_force: source.fetch_status(vehicle_id, force=effective_force),
        force=False,
    )
    return status_obj


def _status(vehicle_id: str, force: bool) -> StatusResponse:
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

    status_obj, wall_fetched, from_cache, forced = cache.get_or_fetch(
        vehicle_id, do_fetch, force=force
    )
    return StatusResponse(
        id=vehicle_id,
        status=status_obj,
        fetched_at=datetime.fromtimestamp(wall_fetched, tz=timezone.utc),
        from_cache=from_cache,
        forced=forced,
    )
