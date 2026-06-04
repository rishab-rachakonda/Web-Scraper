from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .models import ExtractorRule

_TRANSFORMS = {
    "strip": str.strip,
    "lower": str.lower,
    "upper": str.upper,
    "int": lambda x: int(re.sub(r"[^\d\-]", "", x) or "0"),
    "float": lambda x: float(re.sub(r"[^\d.\-]", "", x) or "0"),
    "url": str.strip,
}


class DataExtractor:
    def __init__(self, rules: list[ExtractorRule]):
        self._rules = rules

    def extract_html(self, html: str, base_url: str = "") -> dict[str, Any]:
        soup = BeautifulSoup(html, "lxml")
        result: dict[str, Any] = {}

        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                result["_structured_data"] = json.loads(script.string or "")
                break
            except Exception:
                pass

        for rule in self._rules:
            result[rule.name] = self._apply_html_rule(soup, rule, base_url)

        return result

    def extract_items(self, html: str, item_selector: str, base_url: str = "") -> list[dict[str, Any]]:
        """Emit one record per element matching `item_selector`, with each rule
        evaluated *within* that container. Turns parallel field-lists into
        proper per-item rows."""
        soup = BeautifulSoup(html, "lxml")
        records: list[dict[str, Any]] = []
        for container in soup.select(item_selector):
            record = {rule.name: self._apply_html_rule(container, rule, base_url) for rule in self._rules}
            records.append(record)
        return records

    def _apply_html_rule(self, soup: BeautifulSoup, rule: ExtractorRule, base_url: str) -> Any:
        elements = []

        if rule.selector:
            elements = soup.select(rule.selector)
        elif rule.xpath:
            from lxml import etree
            tree = etree.fromstring(str(soup).encode(), etree.HTMLParser())
            elements = tree.xpath(rule.xpath)

        if not elements:
            return [] if rule.multiple else None

        def get_value(el: Any) -> str:
            if rule.attribute:
                val = el.get(rule.attribute, "") if hasattr(el, "get") else ""
                if rule.attribute in ("href", "src") and base_url and val.startswith("/"):
                    val = urljoin(base_url, val)
                return val
            return el.get_text(separator=" ", strip=True) if hasattr(el, "get_text") else str(el).strip()

        def transform(val: str) -> Any:
            if not rule.transform or not val:
                return val
            fn = _TRANSFORMS.get(rule.transform)
            return fn(val) if fn else val

        if rule.multiple:
            return [transform(get_value(el)) for el in elements]
        return transform(get_value(elements[0]))

    def extract_json(self, data: Any) -> dict[str, Any]:
        if not self._rules:
            if isinstance(data, dict):
                return data
            return {"_data": data}

        result: dict[str, Any] = {}
        for rule in self._rules:
            if rule.jsonpath:
                try:
                    import jmespath
                    result[rule.name] = jmespath.search(rule.jsonpath, data)
                except Exception:
                    result[rule.name] = None
            elif rule.regex and isinstance(data, str):
                m = re.search(rule.regex, data)
                result[rule.name] = m.group(1) if m else None
        return result

    def detect_links(self, html: str, base_url: str, selector: str | None = None) -> list[str]:
        from urllib.parse import urlparse
        soup = BeautifulSoup(html, "lxml")
        base_domain = urlparse(base_url).netloc
        links: list[str] = []

        elements = soup.select(selector) if selector else soup.find_all("a", href=True)
        for el in elements:
            href = el.get("href", "") if hasattr(el, "get") else ""
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue
            full = urljoin(base_url, href)
            if urlparse(full).netloc == base_domain and full not in links:
                links.append(full)

        return links
