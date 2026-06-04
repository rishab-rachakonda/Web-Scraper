"""
Core scraping engine. Wires together:
  - HTTP client selection (curl_cffi TLS | httpx | Playwright browser)
  - Stealth mode
  - Cookie import from browser
  - Concurrent requests / browser pages
  - Auto-pagination (next-page detection + infinite scroll)
  - Adaptive rate limiting
  - Webhook real-time push
"""
from __future__ import annotations

import asyncio
from typing import Callable

import httpx

from .ai_extractor import OutputSchema
from .autoschema import detect_fields, detect_schema
from .extractor import DataExtractor
from .models import JobConfig, ScrapedItem, ScraperStats

_SMART_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_BLOCK_MARKERS = ("just a moment", "cf-browser-verification", "attention required",
                  "checking your browser", "access denied")


class ScraperEngine:
    def __init__(
        self,
        job: JobConfig,
        on_item: Callable[[ScrapedItem], None] | None = None,
        on_stats: Callable[[ScraperStats], None] | None = None,
    ):
        self._job = job
        self._on_item = on_item
        self._on_stats = on_stats
        self._stats = ScraperStats(total_urls=len(job.urls))
        self._visited: set[str] = set()
        self._queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
        self._webhook_buf: list[ScrapedItem] = []
        self.smart_notes: list[str] = []   # human-readable decisions made in smart mode

        if job.output_schema:
            self._extractor = DataExtractor(OutputSchema(job.output_schema).to_rules())
        else:
            self._extractor = DataExtractor(job.rules)

        # Resolve cookies_from before any requests
        self._extra_cookies: dict[str, str] = {}
        if job.cookies_from:
            self._extra_cookies = self._load_browser_cookies(job)

    # ── setup ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _load_browser_cookies(job: JobConfig) -> dict[str, str]:
        try:
            from .cookie_import import import_cookies
            url = job.urls[0] if job.urls else None
            cookies = import_cookies(job.cookies_from, url)
            return cookies
        except Exception as exc:
            print(f"[cookie_import] Warning: {exc}")
            return {}

    def _merge_cookies(self):
        if self._extra_cookies:
            if self._job.auth is None:
                from .models import AuthConfig
                self._job.auth = AuthConfig()
            self._job.auth.cookies.update(self._extra_cookies)

    # ── client selection ──────────────────────────────────────────────────────

    def _pick_http_client(self):
        if self._job.tls_impersonate:
            from .tls_client import TLSClient
            return TLSClient(self._job)
        from .http_client import AsyncHTTPClient
        return AsyncHTTPClient(self._job)

    async def _detect_mode(self) -> bool:
        if self._job.mode == "browser":
            return True
        if self._job.mode == "http":
            return False
        if not self._job.urls:
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as c:
                r = await c.get(self._job.urls[0])
                body = r.text.lower()
                markers = [
                    "__next_data__", "__nuxt__", "react-app",
                    "ng-version", 'id="root">', 'id="app">',
                    "window.__store__", "__REDUX_STATE__",
                ]
                return any(m in body for m in markers)
        except Exception:
            return False

    # ── main entry point ──────────────────────────────────────────────────────

    async def _smart_setup(self):
        """Smart mode: probe the first URL and decide transport + schema, but
        never override anything the user set explicitly."""
        if self._job.mode != "smart" or not self._job.urls:
            return
        url = self._job.urls[0]
        html, blocked = "", False
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True,
                                         headers={"User-Agent": _SMART_UA}) as c:
                r = await c.get(url)
                html = r.text
                low = html.lower()
                blocked = r.status_code in (403, 429) or any(m in low for m in _BLOCK_MARKERS)
        except Exception:
            blocked = True

        # 1. Transport: escalate to TLS impersonation if the site looks protected.
        if blocked and not self._job.tls_impersonate:
            self._job.tls_impersonate = "chrome124"
            self.smart_notes.append("site looks protected → TLS impersonation (chrome124)")
            html = await self._fetch_with_tls(url) or html

        # 2. Schema: auto-detect when the user supplied no extraction rules.
        #    Respect an explicit item_selector — only detect the fields inside it.
        if self._job.rules or self._job.output_schema or not html:
            return
        if self._job.item_selector:
            rules = detect_fields(html, self._job.item_selector, url)
            if rules:
                self._job.rules = rules
                self._extractor = DataExtractor(rules)
                self.smart_notes.append(
                    f"using your item_selector '{self._job.item_selector}'; "
                    f"auto-detected {len(rules)} fields: " + ", ".join(r.name for r in rules))
            else:
                self.smart_notes.append(
                    f"item_selector '{self._job.item_selector}' matched no fields")
            return
        selector, rules = detect_schema(html, url)
        if selector and rules:
            self._job.item_selector = selector
            self._job.rules = rules
            self._extractor = DataExtractor(rules)
            self.smart_notes.append(
                f"auto-detected item_selector '{selector}' with {len(rules)} fields: "
                + ", ".join(r.name for r in rules))
        else:
            self.smart_notes.append("no repeating structure found → whole-page extraction")

    async def _fetch_with_tls(self, url: str) -> str:
        try:
            from .tls_client import TLSClient
            async with TLSClient(self._job) as c:
                r = await c.get(url)
                return r.text
        except Exception:
            return ""

    async def run(self) -> list[ScrapedItem]:
        self._merge_cookies()
        await self._smart_setup()

        for url in self._job.urls:
            await self._queue.put((url, 0))

        use_browser = await self._detect_mode()
        items: list[ScrapedItem] = []

        if use_browser:
            from .browser import BrowserEngine
            async with BrowserEngine(self._job) as browser:
                await self._process_browser(browser, items)
        else:
            client = self._pick_http_client()
            async with client as c:
                if self._job.follow_links or (self._job.pagination and self._job.pagination.auto):
                    await self._process_sequential(c, items)
                else:
                    await self._process_concurrent(c, items)

        # flush any remaining webhook batch
        await self._flush_webhook()
        return items

    # ── HTTP processing ───────────────────────────────────────────────────────

    async def _collect(self, batch: list[ScrapedItem], items: list[ScrapedItem]):
        for item in batch:
            items.append(item)
            await self._notify(item)

    async def _process_sequential(self, client, items: list[ScrapedItem]):
        while not self._queue.empty() and len(self._visited) < self._job.max_pages:
            url, depth = await self._queue.get()
            if url in self._visited:
                continue
            self._visited.add(url)
            await self._collect(await self._scrape_http(client, url, depth), items)

    async def _process_concurrent(self, client, items: list[ScrapedItem]):
        sem = asyncio.Semaphore(self._job.concurrency)
        urls: list[tuple[str, int]] = []

        while not self._queue.empty():
            url, depth = self._queue.get_nowait()
            if url not in self._visited:
                self._visited.add(url)
                urls.append((url, depth))

        async def fetch(url: str, depth: int) -> list[ScrapedItem]:
            async with sem:
                return await self._scrape_http(client, url, depth)

        results = await asyncio.gather(
            *[fetch(u, d) for u, d in urls[: self._job.max_pages]],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, list):
                await self._collect(r, items)

    async def _scrape_http(self, client, url: str, depth: int) -> list[ScrapedItem]:
        try:
            resp = await client.get(url)
            self._stats.bytes_downloaded += len(resp.content)
            ct = resp.headers.get("content-type", "")

            if "application/json" in ct:
                try:
                    data = self._extractor.extract_json(resp.json())
                except Exception:
                    data = self._extractor.extract_json(resp.text)
                return [ScrapedItem(url=url, data=data, status_code=resp.status_code)]

            self._maybe_enqueue_links(resp.text, url, depth)
            await self._maybe_paginate(resp.text, url)
            return self._build_items(resp.text, url, resp.status_code)
        except Exception as exc:
            return [ScrapedItem(url=url, data={}, error=str(exc))]

    def _build_items(self, html: str, url: str, status: int) -> list[ScrapedItem]:
        """One record per `item_selector` match, or a single whole-page record
        when no item_selector is set (or it matches nothing)."""
        if self._job.item_selector:
            records = self._extractor.extract_items(html, self._job.item_selector, url)
            if records:
                return [ScrapedItem(url=url, data=rec, status_code=status) for rec in records]
        data = self._extractor.extract_html(html, url)
        return [ScrapedItem(url=url, data=data, status_code=status)]

    # ── browser processing ────────────────────────────────────────────────────

    async def _process_browser(self, browser, items: list[ScrapedItem]):
        while not self._queue.empty() and len(self._visited) < self._job.max_pages:
            batch: list[tuple[str, int]] = []
            while not self._queue.empty() and len(batch) < self._job.concurrency:
                url, depth = self._queue.get_nowait()
                if url not in self._visited:
                    self._visited.add(url)
                    batch.append((url, depth))

            if not batch:
                break

            results = await asyncio.gather(
                *[self._scrape_browser(browser, u, d) for u, d in batch],
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, list):
                    await self._collect(r, items)

    async def _scrape_browser(self, browser, url: str, depth: int) -> list[ScrapedItem]:
        try:
            content, status = await browser.get_page_content(url)
            self._stats.bytes_downloaded += len(content.encode())
            self._maybe_enqueue_links(content, url, depth)
            await self._maybe_paginate(content, url)
            return self._build_items(content, url, status)
        except Exception as exc:
            return [ScrapedItem(url=url, data={}, error=str(exc))]

    # ── helpers ───────────────────────────────────────────────────────────────

    def _maybe_enqueue_links(self, html: str, url: str, depth: int):
        if not self._job.follow_links or depth >= self._job.max_depth:
            return
        for link in self._extractor.detect_links(html, url, self._job.link_selector):
            if link not in self._visited:
                self._stats.total_urls += 1
                self._queue.put_nowait((link, depth + 1))

    async def _maybe_paginate(self, html: str, url: str):
        cfg = self._job.pagination
        if not cfg or not cfg.auto:
            return
        if not hasattr(self, "_paginators"):
            self._paginators: dict[str, object] = {}
        if url not in self._paginators:
            from .pagination import Paginator
            self._paginators[url] = Paginator(cfg, url)

        # Walk through paginator chain for this seed URL
        root_url = url
        paginator = self._paginators[root_url]
        next_url = paginator.next_url(html, url)  # type: ignore[union-attr]
        if next_url and next_url not in self._visited:
            self._stats.total_urls += 1
            self._queue.put_nowait((next_url, 0))

    async def _notify(self, item: ScrapedItem):
        self._stats.completed += 1
        if item.error:
            self._stats.failed += 1
        else:
            self._stats.items_extracted += 1

        if self._on_item:
            self._on_item(item)
        if self._on_stats:
            self._on_stats(self._stats)

        if self._job.webhook:
            self._webhook_buf.append(item)
            if len(self._webhook_buf) >= self._job.webhook.batch_size:
                await self._flush_webhook()

    async def _flush_webhook(self):
        if not self._job.webhook or not self._webhook_buf:
            return
        cfg = self._job.webhook
        payload = [
            {"url": i.url, "data": i.data, "scraped_at": i.scraped_at.isoformat(),
             "status_code": i.status_code, "error": i.error}
            for i in self._webhook_buf
        ]
        self._webhook_buf.clear()
        try:
            async with httpx.AsyncClient(timeout=cfg.timeout) as c:
                await c.request(
                    cfg.method,
                    cfg.url,
                    json=payload if len(payload) > 1 else payload[0],
                    headers=cfg.headers,
                )
        except Exception as exc:
            print(f"[webhook] Failed to push {len(payload)} items: {exc}")
