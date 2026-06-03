"""
Import cookies from an installed browser (Chrome, Firefox, Edge, Safari).
Lets you scrape sites you're already logged into without re-implementing auth.

Requires:  pip install browser-cookie3
"""
from __future__ import annotations

from urllib.parse import urlparse


def import_cookies(browser: str, url: str | None = None) -> dict[str, str]:
    """
    Return {name: value} cookies from the specified browser.

    Args:
        browser: "chrome" | "firefox" | "edge" | "safari" | "brave" | "chromium"
        url:     If given, only return cookies for that domain.
    """
    try:
        import browser_cookie3  # type: ignore
    except ImportError:
        raise RuntimeError(
            "browser-cookie3 is not installed.\n"
            "Run:  pip install browser-cookie3\n"
            "Note: the browser must NOT be running while cookies are read on Windows."
        )

    domain = None
    if url:
        parsed = urlparse(url)
        domain = parsed.netloc.lstrip("www.")

    loaders = {
        "chrome":   browser_cookie3.chrome,
        "chromium": browser_cookie3.chromium,
        "firefox":  browser_cookie3.firefox,
        "edge":     browser_cookie3.edge,
        "brave":    browser_cookie3.brave,
        "opera":    browser_cookie3.opera,
        "safari":   browser_cookie3.safari,
    }

    loader = loaders.get(browser.lower())
    if not loader:
        raise ValueError(
            f"Unknown browser '{browser}'. "
            f"Supported: {', '.join(loaders)}"
        )

    kwargs = {}
    if domain:
        kwargs["domain_name"] = domain

    try:
        jar = loader(**kwargs)
        return {c.name: c.value for c in jar}
    except Exception as exc:
        raise RuntimeError(
            f"Could not read {browser} cookies: {exc}\n"
            "Make sure the browser is fully closed before running the scraper."
        ) from exc
