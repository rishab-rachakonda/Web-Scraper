"""
Playwright Chromium engine with stealth, parallel pages, and proxy support.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .models import JobConfig

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
    _OK = True
except ImportError:
    _OK = False


class BrowserEngine:
    def __init__(self, job: JobConfig):
        self._job = job
        self._pw = None
        self._browser: Browser | None = None
        self._ctx: BrowserContext | None = None
        self._sem = asyncio.Semaphore(max(1, job.concurrency))

    async def __aenter__(self):
        if not _OK:
            raise RuntimeError(
                "playwright not installed.\n"
                "Run:  pip install playwright && playwright install chromium"
            )
        self._pw = await async_playwright().start()

        launch_args: dict[str, Any] = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-first-run",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        }
        if self._job.proxy and self._job.proxy.urls:
            launch_args["proxy"] = {"server": self._job.proxy.urls[0]}

        self._browser = await self._pw.chromium.launch(**launch_args)
        self._ctx = await self._make_context()
        return self

    async def _make_context(self) -> BrowserContext:
        ctx_args: dict[str, Any] = {
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1920, "height": 1080},
            "locale": "en-US",
            "timezone_id": "America/New_York",
            "extra_http_headers": {
                "Accept-Language": "en-US,en;q=0.9",
            },
        }

        if self._job.auth and self._job.auth.cookies:
            ctx_args["storage_state"] = {
                "cookies": [
                    {"name": k, "value": v, "domain": "", "path": "/"}
                    for k, v in self._job.auth.cookies.items()
                ],
                "origins": [],
            }

        ctx = await self._browser.new_context(**ctx_args)
        # Block heavy assets for speed — but keep images when capturing visuals.
        if not self._job.capture:
            await ctx.route(
                "**/*.{png,jpg,jpeg,gif,svg,ico,woff,woff2,ttf,otf,mp4,webm}",
                lambda r: r.abort(),
            )
        return ctx

    async def __aexit__(self, *_):
        if self._ctx:
            await self._ctx.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def get_page_content(self, url: str) -> tuple[str, int]:
        async with self._sem:
            return await self._fetch_page(url)

    async def _fetch_page(self, url: str) -> tuple[str, int]:
        from .stealth import apply_stealth, randomise_viewport, human_delay

        page: Page = await self._ctx.new_page()
        try:
            if self._job.stealth:
                await apply_stealth(page)
                await randomise_viewport(page)

            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            status = resp.status if resp else 200

            if self._job.stealth:
                await human_delay(0.4, 1.2)
            else:
                await asyncio.sleep(0.3)

            # Infinite scroll if pagination config requests it
            if self._job.pagination and self._job.pagination.infinite_scroll:
                from .pagination import scroll_to_bottom
                content = await scroll_to_bottom(
                    page,
                    pause=self._job.pagination.scroll_pause,
                )
            else:
                content = await page.content()

            if self._job.capture:
                await self._capture(page, url)

            return content, status
        finally:
            await page.close()

    def _capture_path(self, url: str, ext: str) -> Path:
        p = urlparse(url)
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", (p.netloc + p.path)).strip("-")[:60] or "page"
        digest = hashlib.sha256(url.encode()).hexdigest()[:8]
        out_dir = Path(self._job.export.output_dir) / "captures"
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / f"{slug}-{digest}.{ext}"

    async def _capture(self, page: "Page", url: str):
        try:
            if self._job.capture == "pdf":
                await page.emulate_media(media="screen")
                await page.pdf(path=str(self._capture_path(url, "pdf")),
                               print_background=True)
            else:
                await page.screenshot(path=str(self._capture_path(url, "png")),
                                      full_page=True)
        except Exception:
            pass   # capture is best-effort; never fail the scrape over it

    async def login(self, login_url: str, username: str, password: str) -> dict[str, str]:
        from .stealth import apply_stealth

        page = await self._ctx.new_page()
        try:
            if self._job.stealth:
                await apply_stealth(page)

            await page.goto(login_url, wait_until="networkidle")
            await page.fill(
                "input[type='email'], input[type='text'][name*='user'], "
                "input[name*='email'], input[name='username']",
                username,
            )
            await page.fill("input[type='password']", password)
            await page.press("input[type='password']", "Enter")
            await page.wait_for_load_state("networkidle")
            cookies = await self._ctx.cookies()
            return {c["name"]: c["value"] for c in cookies}
        finally:
            await page.close()
