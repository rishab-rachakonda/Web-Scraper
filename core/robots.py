"""robots.txt compliance — opt-in politeness.

Fetches and caches each host's robots.txt and answers can-fetch / crawl-delay
questions for the configured user-agent. Fail-open: if robots.txt is missing or
unreachable, requests are allowed.
"""
from __future__ import annotations

import urllib.robotparser
from urllib.parse import urljoin, urlparse

import httpx


class RobotsChecker:
    def __init__(self, user_agent: str = "*"):
        self._ua = user_agent or "*"
        self._cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    @staticmethod
    def _base(url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"

    async def _load(self, base: str) -> urllib.robotparser.RobotFileParser | None:
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as c:
                resp = await c.get(urljoin(base, "/robots.txt"))
            if resp.status_code != 200 or not resp.text.strip():
                return None
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(resp.text.splitlines())
            return rp
        except Exception:
            return None  # fail-open

    async def allowed(self, url: str) -> bool:
        base = self._base(url)
        if base not in self._cache:
            self._cache[base] = await self._load(base)
        rp = self._cache[base]
        return True if rp is None else rp.can_fetch(self._ua, url)

    async def crawl_delay(self, url: str) -> float | None:
        base = self._base(url)
        if base not in self._cache:
            self._cache[base] = await self._load(base)
        rp = self._cache[base]
        if rp is None:
            return None
        try:
            delay = rp.crawl_delay(self._ua)
            return float(delay) if delay is not None else None
        except Exception:
            return None
