from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class ProxyConfig(BaseModel):
    urls: list[str] = Field(default_factory=list)
    rotate: bool = True


class AuthConfig(BaseModel):
    type: Literal["basic", "bearer", "cookie", "form"] = "cookie"
    username: str | None = None
    password: str | None = None
    token: str | None = None
    cookies: dict[str, str] = Field(default_factory=dict)
    login_url: str | None = None
    login_form_selector: str | None = None


class ExtractorRule(BaseModel):
    name: str
    selector: str | None = None
    xpath: str | None = None
    jsonpath: str | None = None
    regex: str | None = None
    attribute: str | None = None
    multiple: bool = False
    transform: Literal["strip", "lower", "upper", "int", "float", "url"] | None = None


class ExportConfig(BaseModel):
    formats: list[Literal["json", "csv", "sqlite", "terminal"]] = ["json", "terminal"]
    output_dir: str = "output"
    filename: str | None = None
    db_path: str = "output/scraped.db"


class RateLimitConfig(BaseModel):
    requests_per_second: float = 2.0
    burst: int = 5
    delay_between_requests: float = 0.5


class RetryConfig(BaseModel):
    max_retries: int = 3
    backoff_factor: float = 2.0
    retry_on_status: list[int] = [429, 500, 502, 503, 504]


class PaginationConfig(BaseModel):
    auto: bool = True
    max_pages: int = 50
    infinite_scroll: bool = False
    scroll_pause: float = 1.5
    next_selector: str | None = None   # override auto-detection
    url_param: str | None = None       # e.g. "page" → ?page=N


class WebhookConfig(BaseModel):
    url: str
    method: str = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    batch_size: int = 1                # push every N items (1 = real-time)
    timeout: float = 10.0


class JobConfig(BaseModel):
    name: str
    description: str = ""
    urls: list[str]
    mode: Literal["http", "browser", "auto"] = "auto"

    # ── anti-detection ────────────────────────────────────────────────────────
    stealth: bool = False              # apply JS stealth patches in browser mode
    tls_impersonate: str | None = None # e.g. "chrome124" — uses curl_cffi TLS client
    cookies_from: str | None = None   # "chrome" | "firefox" | "edge" — import browser cookies

    # ── crawling ──────────────────────────────────────────────────────────────
    concurrency: int = 5               # parallel requests / browser pages
    follow_links: bool = False
    link_selector: str | None = None
    max_depth: int = 1
    max_pages: int = 100
    pagination: PaginationConfig | None = None

    # ── extraction ────────────────────────────────────────────────────────────
    rules: list[ExtractorRule] = Field(default_factory=list)
    output_schema: str | None = None

    # ── network ───────────────────────────────────────────────────────────────
    headers: dict[str, str] = Field(default_factory=dict)
    auth: AuthConfig | None = None
    proxy: ProxyConfig | None = None
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    adaptive_rate: bool = True         # auto-adjust speed based on responses

    # ── output ────────────────────────────────────────────────────────────────
    export: ExportConfig = Field(default_factory=ExportConfig)
    webhook: WebhookConfig | None = None
    schedule: str | None = None


class ScrapedItem(BaseModel):
    url: str
    data: dict[str, Any]
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status_code: int | None = None
    error: str | None = None


class ScraperStats(BaseModel):
    total_urls: int = 0
    completed: int = 0
    failed: int = 0
    bytes_downloaded: int = 0
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    items_extracted: int = 0

    @property
    def success_rate(self) -> float:
        if self.completed == 0:
            return 0.0
        return (self.completed - self.failed) / self.completed * 100

    @property
    def elapsed_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.start_time).total_seconds()

    @property
    def urls_per_second(self) -> float:
        elapsed = self.elapsed_seconds
        return 0.0 if elapsed == 0 else self.completed / elapsed
