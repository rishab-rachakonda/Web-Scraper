"""
TLS-fingerprinting HTTP client via curl_cffi.

Impersonates a real browser at the TLS/HTTP2 handshake level so WAFs
(Cloudflare, Akamai, DataDome, etc.) that fingerprint the TLS client hello
cannot distinguish this client from a genuine Chrome or Firefox browser.

Drop-in replacement for AsyncHTTPClient: same __aenter__/__aexit__/get()
interface so the engine can swap clients transparently.
"""
from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict

from .models import JobConfig

try:
    from curl_cffi.requests import AsyncSession
    _OK = True
except ImportError:
    _OK = False

# Ordered by prevalence — first two are the default random pool
_IMPERSONATIONS = [
    "chrome124", "chrome110", "chrome107",
    "safari17_0", "safari15_5",
    "firefox117", "firefox110",
]

_UA_MAP = {
    "chrome124": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "chrome110": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
    "safari17_0": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "firefox117": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:117.0) Gecko/20100101 Firefox/117.0",
}
_UA_DEFAULT = _UA_MAP["chrome124"]


class _TokenBucket:
    def __init__(self, rate: float, burst: int):
        self.rate = rate
        self.burst = burst
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
            self._last = now
            if self._tokens < 1:
                await asyncio.sleep((1 - self._tokens) / self.rate)
                self._tokens = 0.0
            else:
                self._tokens -= 1


class _AdaptiveLimiter:
    """Speeds up on sustained success, backs off on 429/errors."""

    def __init__(self, initial_rps: float, min_rps: float = 0.2, max_rps: float = 20.0):
        self._rps = initial_rps
        self._min = min_rps
        self._max = max_rps
        self._window: list[bool] = []

    def record(self, ok: bool):
        self._window.append(ok)
        if len(self._window) > 30:
            self._window.pop(0)
        if len(self._window) >= 10:
            rate = sum(self._window) / len(self._window)
            if rate > 0.92:
                self._rps = min(self._max, self._rps * 1.15)
            elif rate < 0.60:
                self._rps = max(self._min, self._rps * 0.60)

    def backoff(self):
        self._rps = max(self._min, self._rps * 0.40)

    @property
    def delay(self) -> float:
        return 1.0 / self._rps


class TLSClient:
    """curl_cffi-backed async HTTP client with TLS browser impersonation."""

    def __init__(self, job: JobConfig):
        if not _OK:
            raise RuntimeError(
                "curl_cffi is not installed.\n"
                "Run:  pip install curl_cffi"
            )
        self._job = job
        self._impersonate = job.tls_impersonate or random.choice(_IMPERSONATIONS[:3])
        self._limiters: dict[str, _TokenBucket] = defaultdict(
            lambda: _TokenBucket(job.rate_limit.requests_per_second, job.rate_limit.burst)
        )
        # Per-domain adaptive limiter so a 429 on one host doesn't throttle others.
        self._adaptive: dict[str, _AdaptiveLimiter] = defaultdict(
            lambda: _AdaptiveLimiter(job.rate_limit.requests_per_second)
        )
        self._session: AsyncSession | None = None

    async def __aenter__(self):
        headers: dict[str, str] = {
            "User-Agent": _UA_MAP.get(self._impersonate, _UA_DEFAULT),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            **self._job.headers,
        }

        if self._job.auth and self._job.auth.type == "bearer":
            headers["Authorization"] = f"Bearer {self._job.auth.token}"

        cookies: dict[str, str] = {}
        if self._job.auth and self._job.auth.cookies:
            cookies = dict(self._job.auth.cookies)

        proxies = None
        if self._job.proxy and self._job.proxy.urls:
            proxies = {"http": self._job.proxy.urls[0], "https": self._job.proxy.urls[0]}

        self._session = AsyncSession(
            impersonate=self._impersonate,
            headers=headers,
            cookies=cookies,
            proxies=proxies,
            timeout=30,
            verify=False,
            allow_redirects=True,
        )
        return self

    async def __aexit__(self, *_):
        if self._session:
            await self._session.close()

    async def get(self, url: str):
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        await self._limiters[domain].acquire()
        adaptive = self._adaptive[domain]

        if self._job.adaptive_rate:
            await asyncio.sleep(adaptive.delay)
        else:
            await asyncio.sleep(self._job.rate_limit.delay_between_requests)

        retry = self._job.retry
        last_exc: Exception | None = None

        for attempt in range(retry.max_retries + 1):
            try:
                resp = await self._session.get(url)

                if resp.status_code == 429:
                    adaptive.backoff()
                    wait = retry.backoff_factor ** attempt
                    await asyncio.sleep(wait)
                    last_exc = RuntimeError(f"HTTP 429 (rate limited)")
                    continue

                if resp.status_code in retry.retry_on_status:
                    adaptive.record(False)
                    await asyncio.sleep(retry.backoff_factor ** attempt)
                    last_exc = RuntimeError(f"HTTP {resp.status_code}")
                    continue

                adaptive.record(resp.status_code < 400)
                return resp

            except Exception as exc:
                last_exc = exc
                self._adaptive.record(False)
                if attempt < retry.max_retries:
                    await asyncio.sleep(retry.backoff_factor ** attempt)

        raise RuntimeError(f"TLS client failed after {retry.max_retries + 1} attempts: {last_exc}")
