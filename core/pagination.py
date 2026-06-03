"""
Auto-pagination: detects "Next" links by multiple heuristics and handles
infinite-scroll pages by repeatedly scrolling and waiting for new content.
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

_NEXT_TEXT = frozenset([
    "next", "next page", "next »", "next >", ">>", "›", "»",
    "load more", "show more", "more results", "suivant", "siguiente",
])

_NEXT_SELECTORS = [
    "a[rel='next']",
    "link[rel='next']",
    "a.next",
    "a.pagination-next",
    "a.page-next",
    "[aria-label='Next page'] a",
    "[aria-label='Next'] a",
    ".pager-next a",
    ".paginator-next a",
    "#next a",
    "a#next",
    ".next-page a",
    "a[class*='next']",
    "li[class*='next'] a",
]

_PAGE_PARAMS = ["page", "p", "pg", "paged", "pagenum", "start", "offset", "from"]


def find_next_url_html(html: str, current_url: str) -> str | None:
    """Return next-page URL from HTML, or None if not found."""
    soup = BeautifulSoup(html, "lxml")

    # 1. <link rel="next">
    tag = soup.find("link", rel="next")
    if tag and tag.get("href"):
        return urljoin(current_url, tag["href"])

    # 2. Common CSS selectors
    for sel in _NEXT_SELECTORS:
        try:
            el = soup.select_one(sel)
            if el:
                href = el.get("href") or el.find("a", href=True)
                if isinstance(href, str) and href:
                    return urljoin(current_url, href)
        except Exception:
            pass

    # 3. Text-matching on <a> tags
    for a in soup.find_all("a", href=True):
        text = a.get_text(separator=" ", strip=True).lower()
        if text in _NEXT_TEXT:
            return urljoin(current_url, a["href"])

    return None


def increment_page_param(url: str, page_num: int, param: str | None = None) -> str | None:
    """
    If the URL has a recognised page-number query param, return the URL
    with that param incremented to page_num.  Returns None if not found.
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    candidates = [param] if param else _PAGE_PARAMS
    for key in candidates:
        if key in params:
            params[key] = [str(page_num)]
            return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))

    return None


def build_page_url(base_url: str, page_num: int, pattern: str) -> str:
    """
    Build a URL from a user-supplied pattern like
    "https://example.com/items?page={n}" or
    "https://example.com/items/{n}/"
    """
    return pattern.format(n=page_num, page=page_num)


async def scroll_to_bottom(
    page,
    pause: float = 1.5,
    max_scrolls: int = 30,
) -> str:
    """
    Scroll a browser page to the bottom to trigger infinite-scroll loading.
    Returns final page HTML after all content has loaded.
    """
    prev_height = -1
    for _ in range(max_scrolls):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(pause)
        height: int = await page.evaluate("document.body.scrollHeight")
        if height == prev_height:
            break
        prev_height = height

    return await page.content()


class Paginator:
    """
    Stateful paginator: given each page's HTML and URL, emits the next URL
    (or None when pagination ends).
    """

    def __init__(self, cfg, start_url: str):
        self._cfg = cfg
        self._page_num = 1
        self._start_url = start_url

    def next_url(self, html: str, current_url: str) -> str | None:
        if self._page_num >= self._cfg.max_pages:
            return None

        self._page_num += 1

        # If user gave an explicit URL pattern
        if self._cfg.url_param:
            url = increment_page_param(current_url, self._page_num, self._cfg.url_param)
            if url and url != current_url:
                return url

        # Try HTML-based detection
        if self._cfg.next_selector:
            soup = BeautifulSoup(html, "lxml")
            el = soup.select_one(self._cfg.next_selector)
            if el and el.get("href"):
                return urljoin(current_url, el["href"])

        url = find_next_url_html(html, current_url)
        if url and url != current_url:
            return url

        # Fallback: try incrementing any recognised page param in the URL
        url = increment_page_param(current_url, self._page_num)
        if url and url != current_url:
            return url

        return None
