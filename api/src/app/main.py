import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import jobs
from .config import get_cors_origins, get_photos_uri, redact_uri
from .database import init_db, start_backfill
from .logging_config import configure_logging
from .tracing import configure_tracing
from .routers import descriptions, faces, health, individuals, jobs as jobs_router, locations, photos, sync, tags

configure_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Luminary API; photos backend: %s", redact_uri(get_photos_uri()))
    # Schema only (fast, idempotent DDL) -- row-level backfills run on a
    # background thread instead, so a large library can't hold startup past
    # the liveness probe. See database.backfill_data().
    init_db()
    start_backfill()
    # Connects on a background thread, and only logs if MLflow can't be
    # reached -- tracing never holds up or fails startup. See tracing.py.
    configure_tracing()
    yield
    logger.info("Shutting down Luminary API")


app = FastAPI(title="Luminary API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(jobs.JobBusy)
async def job_busy(request: Request, exc: jobs.JobBusy) -> JSONResponse:
    # Only one background job (sync / face matching / descriptions /
    # locations) runs at a time, whoever asks -- see jobs.py.
    return JSONResponse(
        status_code=409,
        content={"detail": f"Another job is already running ({exc.active}). Wait for it to finish or cancel it."},
    )


@app.exception_handler(Exception)
async def log_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(health.router, prefix="/api")
app.include_router(photos.router, prefix="/api")
app.include_router(sync.router, prefix="/api")
app.include_router(jobs_router.router, prefix="/api")
app.include_router(individuals.router, prefix="/api")
app.include_router(faces.router, prefix="/api")
app.include_router(descriptions.router, prefix="/api")
app.include_router(locations.router, prefix="/api")
app.include_router(tags.router, prefix="/api")
