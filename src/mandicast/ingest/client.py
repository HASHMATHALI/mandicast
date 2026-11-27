"""HTTP client for the data.gov.in AGMARKNET resource.

Behaviour verified against the live API in Phase 1 (docs/02_data_source_assessment.md §3, §4, §8):
an explicit User-Agent is required, `limit` up to 10,000 works, CSV pages sorted by `Arrival_Date` are
repeatable, and transient failures (HTTP 504, resets, DNS errors) recover with retries.

The API key is sent only in the query string and never appears in exceptions or logs raised from here.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

log = logging.getLogger(__name__)
RETRYABLE_HTTP = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    """Raised with a message that never contains the request URL or key."""


class DataGovClient:
    def __init__(self, base_url: str, api_key: str, user_agent: str, timeout_s: int = 300, retries: int = 10,
                 backoff_base_s: float = 5.0, backoff_max_s: float = 600.0,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if not api_key:
            raise ValueError("DATA_GOV_IN_API_KEY is not set (see .env.example)")
        self._base_url = base_url
        self._key = api_key
        self._ua = user_agent
        self._timeout = timeout_s
        self._retries = retries
        self._backoff_base = backoff_base_s
        self._backoff_max = backoff_max_s
        self._sleep = sleep

    def _get(self, params: dict) -> bytes:
        query = urllib.parse.urlencode({"api-key": self._key, **params})
        req = urllib.request.Request(f"{self._base_url}?{query}", headers={"User-Agent": self._ua})  # noqa: S310
        for attempt in range(self._retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 - https base URL
                    return resp.read()
            except urllib.error.HTTPError as exc:
                reason = f"HTTP {exc.code}"
                if exc.code not in RETRYABLE_HTTP:
                    raise FetchError(f"{reason} for {params}") from None
            except (TimeoutError, urllib.error.URLError, ConnectionError) as exc:
                detail = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                reason = f"{type(exc).__name__} ({detail})"
            if attempt == self._retries:
                raise FetchError(f"{reason} after {self._retries} retries for {params}")
            wait = min(self._backoff_max, self._backoff_base * 2 ** attempt)
            log.warning("%s for %s; retry %d in %.0fs", reason, params, attempt + 1, wait)
            self._sleep(wait)
        raise AssertionError("unreachable")

    def csv_page(self, commodity: str, offset: int, limit: int) -> bytes:
        return self._get({"format": "csv", "limit": limit, "offset": offset, "filters[Commodity]": commodity,
                          "sort[Arrival_Date]": "asc"})

    def csv_date(self, commodity: str, arrival_date_ddmmyyyy: str, limit: int) -> bytes:
        return self._get({"format": "csv", "limit": limit, "offset": 0, "filters[Commodity]": commodity,
                          "filters[Arrival_Date]": arrival_date_ddmmyyyy})

    def total(self, commodity: str) -> int:
        body = json.loads(self._get({"format": "json", "limit": 1, "filters[Commodity]": commodity}))
        return int(body["total"])
