import asyncio
import logging
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import Worker
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.sessions import SessionMiddleware

from app.audit import set_request_context
from app.config import settings
from app.db import close_db, init_db
from app.routers import audit, auth, documents, observations, patients, review
from app.worker import process_document

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger("labledger")

limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open Mongo and the arq queue for the process lifetime."""
    await init_db()
    logger.info("connected to mongo db=%s", settings.mongo_db_name)
    # A dead queue must not take the API down: uploads still store the file and
    # can be replayed with POST /api/documents/{id}/reprocess.
    try:
        app.state.arq = await create_pool(
            RedisSettings.from_dsn(settings.redis_url),
            default_queue_name=settings.arq_queue_name,
        )
        logger.info("arq pool ready queue=%s", settings.arq_queue_name)
    except Exception as exc:  # noqa: BLE001 - a dead queue must never stop the API booting
        app.state.arq = None
        logger.warning("arq unavailable (%s): uploads will queue without a worker",
                       type(exc).__name__)

    worker, worker_task = None, None
    if settings.run_worker_in_api:
        # No on_startup/on_shutdown: this process already opened Mongo above
        # and closes it below, and running the worker's own hooks would
        # re-initialise Beanie underneath the live request handlers.
        #
        # handle_signals=False is not optional. arq installs SIGINT/SIGTERM
        # handlers of its own by default, which would take the shutdown out of
        # uvicorn's hands and leave requests in flight when the platform stops
        # the service.
        worker = Worker(
            functions=[process_document],
            redis_settings=RedisSettings.from_dsn(settings.redis_url),
            queue_name=settings.arq_queue_name,
            max_jobs=2,     # sharing an event loop with request handling
            job_timeout=300,
            handle_signals=False,
        )
        worker_task = asyncio.create_task(worker.async_run())
        logger.info("arq worker running in-process queue=%s", settings.arq_queue_name)

    yield

    if worker is not None:
        await worker.close()
        worker_task.cancel()
    if app.state.arq is not None:
        await app.state.arq.aclose()
    await close_db()


app = FastAPI(title="LabLedger API", version="0.1.0", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Explicit origin, credentials on. Never "*" with credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Authlib stores the OAuth state here. Short-lived, signed, httponly.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.jwt_secret,
    max_age=600,
    same_site="lax",
    https_only=settings.is_prod,
)

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(observations.router)
app.include_router(review.router)
app.include_router(patients.router)
app.include_router(audit.router)


@app.middleware("http")
async def security_headers(request, call_next):
    """Attach hardening headers, and open the audit context for this request."""
    # Set before the route runs, so anything downstream — including repo.py —
    # can attribute an access without being handed the request object.
    set_request_context(
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    # API returns JSON only; the SPA ships its own CSP from the frontend host.
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    # Every response here can carry PHI — results, dates of birth, MRNs — and
    # without this they are cacheable by default: by the browser's disk cache,
    # and by any intermediary proxy on a corporate network. `private` keeps
    # shared caches out, `no-store` keeps it off disk entirely. setdefault, so
    # a route that already chose its own value (the PDF download) keeps it.
    response.headers.setdefault("Cache-Control", "private, no-store")
    if settings.is_prod:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# HEAD as well as GET. Uptime monitors send HEAD by default — it is the
# cheapest way to ask "is this alive" — and FastAPI registers only the method
# named, so a GET-only probe answers 405 and every monitor reads that as an
# outage. The service was never down; the check was asking a question the route
# had not been told to answer. HTTP says HEAD is valid wherever GET is.
@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health():
    """Liveness probe. Deliberately unauthenticated and free of any detail."""
    return {"status": "ok", "env": settings.env}
