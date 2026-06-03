from __future__ import annotations

import asyncio
import time
from collections import defaultdict

import httpx

from .models import JobConfig

_FALLBACK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

try:
    from fake_useragent import UserAgent as _UA
    _ua = _UA()

    def _random_ua() -> str:
        try:
            return _ua.random
        except Exception:
            return _FALLBACK_UA
except ImportError:
    def _random_ua() -> str:
        return _FALLBACK_UA


class _TokenBucket:
    def __init__(self, rate: float, burst: int):
        self.rate = rate
        self.burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            self._tokens = min(
                self.burst,
                self._tokens + (now - self._last_refill) * self.rate,
            )
            self._last_refill = now
            if self._tokens < 1:
                await asyncio.sleep((1 - self._tokens) / self.rate)
                self._tokens = 0.0
            else:
                self._tokens -= 1


class _ProxyRotator:
    def __init__(self, proxies: list[str]):
        self._proxies = proxies
        self._idx = 0
        self._lock = asyncio.Lock()

    async def next(self) -> str | None:
        if not self._proxies:
            return None
        async with self._lock:
            proxy = self._proxies[self._idx % len(self._proxies)]
            self._idx += 1
            return proxy


class AsyncHTTPClient:
    def __init__(self, job: JobConfig):
        self._job = job
        self._limiters: dict[str, _TokenBucket] = defaultdict(
            lambda: _TokenBucket(
                job.rate_limit.requests_per_second,
                job.rate_limit.burst,
            )
        )
        self._proxies = _ProxyRotator(job.proxy.urls if job.proxy else [])
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        headers: dict[str, str] = {"User-Agent": _random_ua(), **self._job.headers}
        auth = None
        if self._job.auth:
            if self._job.auth.type == "basic":
                auth = (self._job.auth.username or "", self._job.auth.password or "")
            elif self._job.auth.type == "bearer":
                headers["Authorization"] = f"Bearer {self._job.auth.token}"

        self._client = httpx.AsyncClient(
            headers=headers,
            auth=auth,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),
            http2=True,
        )
        if self._job.auth and self._job.auth.cookies:
            for name, val in self._job.auth.cookies.items():
                self._client.cookies.set(name, val)
        return self

    async def __aexit__(self, *_):
        if self._client:
            await self._client.aclose()

    async def get(self, url: str) -> httpx.Response:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        await self._limiters[domain].acquire()
        await asyncio.sleep(self._job.rate_limit.delay_between_requests)

        proxy = await self._proxies.next()
        retry = self._job.retry
        last_exc: Exception | None = None

        for attempt in range(retry.max_retries + 1):
            if attempt > 0:
                self._client.headers["User-Agent"] = _random_ua()
            try:
                kwargs: dict = {}
                if proxy:
                    kwargs["extensions"] = {"sni_hostname": None}
                resp = await self._client.get(url)
                if resp.status_code in retry.retry_on_status:
                    await asyncio.sleep(retry.backoff_factor ** attempt)
                    last_exc = RuntimeError(f"HTTP {resp.status_code}")
                    continue
                return resp
            except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError) as exc:
                last_exc = exc
                if attempt < retry.max_retries:
                    await asyncio.sleep(retry.backoff_factor ** attempt)

        raise RuntimeError(f"Failed after {retry.max_retries + 1} attempts: {last_exc}")
