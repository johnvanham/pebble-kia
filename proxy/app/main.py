from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse

from .auth import verify_bearer
from .cache import StatusCache
from .config import Settings, load_settings
from .models import StatusResponse, VehicleList
from .sources.base import DataSource, VehicleNotFound
from .sources.demo import DemoDataSource
from .sources.live import LiveDataSource, LiveNotYetImplemented


def _build_source(settings: Settings) -> DataSource:
    if settings.data_source == "demo":
        return DemoDataSource(settings.demo_data_file)
    if settings.data_source == "live":
        return LiveDataSource()
    raise RuntimeError(f"unknown DATA_SOURCE: {settings.data_source}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    app.state.settings = settings
    app.state.source = _build_source(settings)
    cache_seconds = (
        settings.demo_refresh_min_seconds
        if settings.data_source == "demo"
        else settings.live_refresh_min_seconds
    )
    app.state.cache = StatusCache(cache_seconds)
    yield


app = FastAPI(title="pebble-kia-proxy", version="0.1.0", lifespan=lifespan)


@app.exception_handler(LiveNotYetImplemented)
async def _live_stub_handler(_req, exc: LiveNotYetImplemented):
    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content={"detail": str(exc)},
    )


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


def _status(vehicle_id: str, force: bool) -> StatusResponse:
    source: DataSource = app.state.source
    cache: StatusCache = app.state.cache

    def do_fetch():
        try:
            return source.fetch_status(vehicle_id)
        except VehicleNotFound:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"vehicle not found: {vehicle_id}",
            )

    status_obj, wall_fetched, from_cache = cache.get_or_fetch(
        vehicle_id, do_fetch, force=force
    )
    return StatusResponse(
        id=vehicle_id,
        status=status_obj,
        fetched_at=datetime.fromtimestamp(wall_fetched, tz=timezone.utc),
        from_cache=from_cache,
    )
