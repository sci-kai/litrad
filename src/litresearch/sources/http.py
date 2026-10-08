"""Shared HTTP client: on-disk response cache, per-host rate limits, retries."""

import hashlib
import json
import logging
import sqlite3
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from litresearch import __version__

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    pass


class ResponseCache:
    """Tiny SQLite key/value store for JSON responses."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # the web UI may create and close the cache in different worker threads;
        # each cache object is still only used by one request at a time
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, stored REAL, body TEXT)"
        )

    def get(self, key: str, max_age: timedelta | None):
        row = self._db.execute("SELECT stored, body FROM cache WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        if max_age is not None and time.time() - row[0] > max_age.total_seconds():
            return None
        return json.loads(row[1])

    def put(self, key: str, body) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (key, time.time(), json.dumps(body))
        )
        self._db.commit()

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None


class HttpClient:
    """JSON HTTP client used by all data sources.

    ``min_interval`` maps host names to the minimal number of seconds between
    two requests. ``transport`` and ``sleep`` can be replaced in tests.
    """

    def __init__(
        self,
        cache: ResponseCache | None = None,
        contact_email: str | None = None,
        min_interval: dict[str, float] | None = None,
        max_retries: int = 5,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ):
        agent = f"litresearch/{__version__}"
        if contact_email:
            agent += f" (mailto:{contact_email})"
        self._client = httpx.Client(
            headers={"User-Agent": agent},
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )
        self.cache = cache
        self.min_interval = min_interval or {}
        self.max_retries = max_retries
        self._sleep = sleep
        self._last_call: dict[str, float] = {}

    def _throttle(self, host: str) -> None:
        interval = self.min_interval.get(host, 0.0)
        wait = self._last_call.get(host, 0.0) + interval - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        self._last_call[host] = time.monotonic()

    @staticmethod
    def _cache_key(method: str, url: str, params, body) -> str:
        raw = json.dumps([method, url, params, body], sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def request(
        self,
        method: str,
        url: str,
        params: dict | None = None,
        json_body=None,
        headers: dict | None = None,
        max_age: timedelta | None = timedelta(days=30),
        use_cache: bool = True,
        allow_404: bool = False,
    ):
        """Return the decoded JSON body (None for a permitted 404)."""
        key = self._cache_key(method, url, params, json_body)
        if use_cache and self.cache is not None:
            cached = self.cache.get(key, max_age)
            if cached is not None:
                return cached

        host = urlsplit(url).hostname or ""
        for attempt in range(self.max_retries + 1):
            self._throttle(host)
            try:
                response = self._client.request(
                    method, url, params=params, json=json_body, headers=headers
                )
            except httpx.TransportError as error:
                if attempt == self.max_retries:
                    raise HttpError(f"{method} {url}: {error}") from error
                self._sleep(min(2**attempt, 60))
                continue
            if response.status_code in RETRY_STATUS and attempt < self.max_retries:
                delay = _retry_after(response) or min(2**attempt, 60)
                log.info(
                    "%s %s -> %s, retrying in %.0f s", method, host, response.status_code, delay
                )
                self._sleep(delay)
                continue
            if response.status_code == 404 and allow_404:
                return None
            if response.status_code >= 400:
                raise HttpError(
                    f"{method} {url}: HTTP {response.status_code} {response.text[:200]}"
                )
            body = response.json()
            if use_cache and self.cache is not None:
                self.cache.put(key, body)
            return body
        raise HttpError(f"{method} {url}: retries exhausted")  # pragma: no cover

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, json_body, **kwargs):
        return self.request("POST", url, json_body=json_body, **kwargs)

    def close(self) -> None:
        self._client.close()
        if self.cache is not None:
            self.cache.close()


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    try:
        return min(float(value), 300.0) if value else None
    except ValueError:
        return None
