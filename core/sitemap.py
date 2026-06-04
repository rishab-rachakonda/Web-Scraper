"""Sitemap discovery — seed a crawl from a site's sitemap.xml.

Looks for /sitemap.xml and any `Sitemap:` entries in robots.txt, follows
sitemap-index files one level deep, and returns the listed page URLs.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx

_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"


async def _seed_sitemaps(client: httpx.AsyncClient, base: str) -> list[str]:
    seeds = [urljoin(base, "/sitemap.xml")]
    try:
        robots = await client.get(urljoin(base, "/robots.txt"))
        if robots.status_code == 200:
            for line in robots.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    seeds.append(line.split(":", 1)[1].strip())
    except Exception:
        pass
    # de-dupe, preserve order
    return list(dict.fromkeys(seeds))


async def discover_sitemap_urls(start_url: str, limit: int = 1000) -> list[str]:
    """Return up to `limit` page URLs listed in the site's sitemap(s)."""
    p = urlparse(start_url)
    base = f"{p.scheme}://{p.netloc}"
    found: list[str] = []
    visited: set[str] = set()

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True,
                                 headers={"User-Agent": _UA}) as client:
        queue = await _seed_sitemaps(client, base)
        while queue and len(found) < limit:
            sm_url = queue.pop(0)
            if sm_url in visited:
                continue
            visited.add(sm_url)
            try:
                resp = await client.get(sm_url)
            except Exception:
                continue
            if resp.status_code != 200:
                continue
            locs = [m.strip() for m in _LOC_RE.findall(resp.text)]
            if "<sitemapindex" in resp.text.lower():
                queue.extend(loc for loc in locs if loc not in visited)   # nested sitemaps
            else:
                for loc in locs:
                    if loc not in found:
                        found.append(loc)
                        if len(found) >= limit:
                            break
    return found
