"""Rate limiting and response caching (docs/07 §1).

- **Rate limit**: fixed one-minute window per client, counted in Redis (shared by all API workers). Client
  addresses are stored only as a keyed hash (HMAC-SHA-256), never in clear. If Redis is unreachable, an in-process
  counter keeps limiting (per worker) and /v1/health reports Redis as down.
- **Cache**: successful GET responses under /v1 are stored in Redis, keyed by path, sorted query and the data
  version (id of the latest successful job: ingestion, cleaning or forecast publication), so new data makes old
  entries unreachable; they expire after the TTL. Without Redis, nothing is cached.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from collections.abc import Callable

from redis import asyncio as aioredis

log = logging.getLogger("mandicast.api")
WINDOW_S = 60


class RateLimiter:
    def __init__(self, limit: int, redis: aioredis.Redis | None, key_secret: bytes) -> None:
        self.limit, self.redis, self.key_secret = limit, redis, key_secret
        self._mem: dict[tuple[str, int], int] = {}
        self._lock = threading.Lock()
        self.redis_ok = redis is not None

    def client_key(self, client: str) -> str:
        return hmac.new(self.key_secret, client.encode(), hashlib.sha256).hexdigest()[:32]

    async def hit(self, client: str, now: float | None = None) -> tuple[bool, int, int]:
        """Count one request. Returns (allowed, remaining, seconds until the window resets)."""
        now = time.time() if now is None else now
        window = int(now // WINDOW_S)
        reset = int(WINDOW_S - (now % WINDOW_S)) or 1
        key = self.client_key(client)
        count = None
        if self.redis is not None:
            try:
                pipe = self.redis.pipeline(transaction=True)
                pipe.incr(f"mc:rl:{key}:{window}")
                pipe.expire(f"mc:rl:{key}:{window}", WINDOW_S + 5)
                count = int((await pipe.execute())[0])
                if not self.redis_ok:
                    log.info("rate limiter: Redis reachable again")
                self.redis_ok = True
            except Exception as exc:  # noqa: BLE001 - any Redis failure falls back to in-process counting
                if self.redis_ok:
                    log.warning("rate limiter: Redis unavailable (%s); counting in process", type(exc).__name__)
                self.redis_ok = False
        if count is None:
            with self._lock:
                if len(self._mem) > 50_000:
                    self._mem = {k: v for k, v in self._mem.items() if k[1] >= window}
                count = self._mem[(key, window)] = self._mem.get((key, window), 0) + 1
        return count <= self.limit, max(0, self.limit - count), reset


class ResponseCache:
    def __init__(self, redis: aioredis.Redis | None, ttl_s: int) -> None:
        self.redis, self.ttl_s = redis, ttl_s

    @property
    def enabled(self) -> bool:
        return self.redis is not None and self.ttl_s > 0

    @staticmethod
    def key(version: str, path: str, query: list[tuple[str, str]]) -> str:
        q = "&".join(f"{k}={v}" for k, v in sorted(query))
        return "mc:cache:" + hashlib.sha256(f"{version}|{path}|{q}".encode()).hexdigest()

    async def get(self, key: str) -> bytes | None:
        if not self.enabled:
            return None
        try:
            return await self.redis.get(key)
        except Exception as exc:  # noqa: BLE001 - a cache failure must never fail the request
            log.debug("cache get failed: %s", type(exc).__name__)
            return None

    async def set(self, key: str, body: bytes) -> None:
        if not self.enabled or len(body) > 2_000_000:
            return
        try:
            await self.redis.set(key, body, ex=self.ttl_s)
        except Exception as exc:  # noqa: BLE001 - a cache failure must never fail the request
            log.debug("cache set failed: %s", type(exc).__name__)


class DataVersion:
    """Latest successful job id, read at most every `ttl_s` seconds per process."""

    def __init__(self, read: Callable[[], int], ttl_s: float = 10.0) -> None:
        self.read, self.ttl_s = read, ttl_s
        self._value: str | None = None
        self._at = 0.0
        self._lock = threading.Lock()

    def get(self) -> str:
        with self._lock:
            if self._value is None or time.monotonic() - self._at > self.ttl_s:
                self._value = str(self.read())
                self._at = time.monotonic()
            return self._value
