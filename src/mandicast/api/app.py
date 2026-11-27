"""MandiCast HTTP API (FastAPI). Run: `python -m mandicast api` (uvicorn, 127.0.0.1:8000); docs at /docs.

Security and operations:
- Database connections are read-only (`default_transaction_read_only`) with a statement timeout; SQL uses bound
  parameters only.
- Per-client rate limit (429 + Retry-After) and a Redis response cache invalidated by new data (limits.py).
- Strict query parameters (unknown or repeated ones are rejected), validated types and ranges, one error format.
- Security headers on every response; CORS only for configured origins (GET, POST, DELETE).
- Account endpoints (sessions, alerts) use a separate writable engine and are never cached.
- JSON access log without client addresses or query values; errors logged with a request id, never returned.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from contextlib import asynccontextmanager

from anyio import to_thread
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from redis import asyncio as aioredis
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

from mandicast import __version__
from mandicast.api.deps import strict_query
from mandicast.api.errors import (
    ApiError,
    api_error_handler,
    error_body,
    http_error_handler,
    internal_error,
    validation_error_handler,
)
from mandicast.api.limits import DataVersion, RateLimiter, ResponseCache
from mandicast.api.routes import account, discovery, forecasts, meta, prices
from mandicast.config import Settings, get_settings
from mandicast.logging_setup import register_secret, setup_logging

log = logging.getLogger("mandicast.api")
access_log = logging.getLogger("mandicast.api.access")
UNCACHED = {"/v1/health"}
PRIVATE_PREFIXES = ("/v1/alerts", "/v1/me", "/v1/auth")  # per-user data: never cached, never shared
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                    "Cross-Origin-Resource-Policy": "same-site"}
JSON_CSP = "default-src 'none'; frame-ancestors 'none'"


def read_only_engine(settings: Settings) -> Engine:
    url = settings.database_url.get_secret_value()
    options = f"-c default_transaction_read_only=on -c statement_timeout={settings.api_statement_timeout_ms}"
    return create_engine(url, pool_pre_ping=True, hide_parameters=True, pool_size=10, max_overflow=5,
                         connect_args={"options": options, "application_name": "mandicast-api",
                                       "connect_timeout": settings.db_connect_timeout_s})


def writable_engine(settings: Settings) -> Engine:
    """For the account endpoints only (sessions, alerts); same statement timeout as the read engine."""
    url = settings.database_url.get_secret_value()
    return create_engine(url, pool_pre_ping=True, hide_parameters=True, pool_size=5, max_overflow=5,
                         connect_args={"options": f"-c statement_timeout={settings.api_statement_timeout_ms}",
                                       "application_name": "mandicast-api-rw",
                                       "connect_timeout": settings.db_connect_timeout_s})


def is_private(request: Request) -> bool:
    return request.url.path.startswith(PRIVATE_PREFIXES) or "authorization" in request.headers


def client_address(request: Request, trust_forwarded_for: bool) -> str:
    if trust_forwarded_for:
        xff = request.headers.get("x-forwarded-for", "")
        # Only the address appended by our own proxy (the last one) can be trusted; earlier ones are client-supplied.
        last = xff.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def create_app(settings: Settings | None = None, engine: Engine | None = None,
               redis: aioredis.Redis | None = None, use_redis: bool = True,
               rw_engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging()
    engine = engine or read_only_engine(settings)
    rw_engine = rw_engine or writable_engine(settings)
    if redis is None and use_redis and settings.redis_url is not None:
        register_secret(settings.redis_url.get_secret_value())
        redis = aioredis.from_url(settings.redis_url.get_secret_value(), socket_timeout=0.5,
                                  socket_connect_timeout=0.5, health_check_interval=30)
    key_secret = hashlib.sha256(b"mandicast-rate-limit|" + (
        settings.redis_url.get_secret_value() if settings.redis_url else settings.database_url.get_secret_value()
    ).encode()).digest()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if app.state.redis is not None:
            await app.state.redis.aclose()

    app = FastAPI(title="MandiCast API", version=__version__, lifespan=lifespan,
                  description="Daily mandi prices from AGMARKNET (Open Government Data Platform India, GODL-India) "
                              "and 7/14-day price forecasts with measured accuracy. Forecasts are estimates, "
                              "not guarantees.",
                  dependencies=[Depends(strict_query)])
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.rw_session_factory = sessionmaker(bind=rw_engine, expire_on_commit=False)
    app.state.redis = redis
    app.state.limiter = RateLimiter(settings.api_rate_limit_per_minute, redis, key_secret)
    app.state.cache = ResponseCache(redis, settings.api_cache_ttl_s)

    def _latest_job() -> int:
        with engine.connect() as c:
            return c.execute(text("SELECT coalesce(max(id), 0) FROM ingestion_jobs WHERE status = 'succeeded'"))\
                .scalar_one()

    app.state.data_version = DataVersion(_latest_job)

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    for r in (discovery.router, prices.router, forecasts.router, meta.router, account.router):
        app.include_router(r, prefix="/v1")

    @app.middleware("http")
    async def gateway(request: Request, call_next):
        rid = uuid.uuid4().hex[:16]
        t0 = time.perf_counter()
        path = request.url.path
        cache_state = "-"
        headers: dict[str, str] = {}
        try:
            if path.startswith("/v1/"):
                allowed, remaining, reset = await app.state.limiter.hit(
                    client_address(request, settings.api_trust_forwarded_for))
                headers.update({"X-RateLimit-Limit": str(settings.api_rate_limit_per_minute),
                                "X-RateLimit-Remaining": str(remaining), "X-RateLimit-Reset": str(reset)})
                if not allowed:
                    response = JSONResponse(error_body("rate_limited", "Too many requests; try again later.",
                                                       {"retry_after_s": reset}), status_code=429,
                                            headers={"Retry-After": str(reset)})
                else:
                    response = await _cached(request, call_next, path)
                    cache_state = response.headers.get("X-Cache", "-")
            else:
                response = await call_next(request)
        except Exception:
            log.exception("unhandled error (request %s, %s %s)", rid, request.method, path)
            response = internal_error(rid)
        response.headers.update(headers)
        response.headers.update(SECURITY_HEADERS)
        response.headers["X-Request-ID"] = rid
        if path.startswith("/v1/"):
            response.headers["Content-Security-Policy"] = JSON_CSP
            response.headers["Cache-Control"] = ("public, max-age=60" if response.status_code == 200
                                                 and path not in UNCACHED and not is_private(request)
                                                 else "no-store")
        access_log.info("request", extra={"fields": {
            "request_id": rid, "method": request.method, "path": path, "status": response.status_code,
            "ms": round(1000 * (time.perf_counter() - t0), 1), "cache": cache_state}})
        return response

    async def _cached(request: Request, call_next, path: str) -> Response:
        cache: ResponseCache = app.state.cache
        if request.method != "GET" or path in UNCACHED or is_private(request) or not cache.enabled:
            return await call_next(request)
        try:
            version = await to_thread.run_sync(app.state.data_version.get)
        except Exception:  # noqa: BLE001 - without a data version, serve uncached
            return await call_next(request)
        key = cache.key(version, path, request.query_params.multi_items())
        body = await cache.get(key)
        if body is not None:
            return Response(body, media_type="application/json", headers={"X-Cache": "HIT"})
        response = await call_next(request)
        if response.status_code != 200:
            return response
        chunks = [chunk async for chunk in response.body_iterator]
        body = b"".join(chunks)
        await cache.set(key, body)
        hdrs = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length",)}
        hdrs["X-Cache"] = "MISS"
        return Response(body, status_code=200, headers=hdrs, media_type=response.media_type)

    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                           allow_methods=["GET", "POST", "DELETE"],
                           allow_headers=["Accept", "Content-Type", "Authorization"], max_age=600,
                           expose_headers=["X-Request-ID", "X-RateLimit-Remaining", "Retry-After"])

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {"name": "MandiCast API", "version": __version__, "docs": "/docs", "health": "/v1/health"}

    return app
