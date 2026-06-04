"""Rule-based auto-schema detection — no LLM.

Given a page's HTML, find the dominant *repeating structure* (product cards,
search-result rows, list items, ...) and propose an `item_selector` plus a set
of field `rules`, so the scraper can extract clean per-item records with zero
hand-written selectors.

Heuristic, in short:
  1. For every element, find parents whose direct children repeat the same
     tag+class "signature" 3+ times — those children are candidate items.
  2. Score each candidate by (repetition count x text richness) and keep the best.
  3. Inside a sample item, derive one field per distinctive class / link / image.
"""
from __future__ import annotations

from collections import Counter

from bs4 import BeautifulSoup, Tag

from .models import ExtractorRule

# Container-ish classes that are layout noise, not meaningful field names.
_NOISE_CLASSES = {
    "row", "col", "container", "wrapper", "clearfix", "d-flex", "flex",
}
_FIELDISH_TAGS = {"h1", "h2", "h3", "h4", "h5", "a", "span", "p", "time", "img"}


def _signature(el: Tag) -> tuple[str, tuple[str, ...]]:
    classes = tuple(sorted(c for c in (el.get("class") or [])))
    return (el.name, classes)


def _selector_for(tag: str, classes: tuple[str, ...]) -> str:
    # Prefer the most specific stable class; fall back to all classes.
    return tag + "".join(f".{c}" for c in classes)


def _clean_name(raw: str, used: set[str]) -> str:
    name = raw.lower().replace("-", "_").replace(" ", "_")
    name = "".join(ch for ch in name if ch.isalnum() or ch == "_").strip("_") or "field"
    base, i = name, 2
    while name in used:
        name, i = f"{base}_{i}", i + 1
    used.add(name)
    return name


def _detect_fields(sample: Tag, base_url: str) -> list[ExtractorRule]:
    rules: list[ExtractorRule] = []
    used_names: set[str] = set()
    seen_selectors: set[str] = set()

    for el in sample.find_all(True):
        if el is sample:
            continue
        classes = [c for c in (el.get("class") or []) if c not in _NOISE_CLASSES]
        text = el.get_text(" ", strip=True)

        # Decide a selector + field name for this element.
        if classes:
            selector = "." + classes[0]
            name_src = classes[0]
        elif el.name in _FIELDISH_TAGS:
            selector = el.name
            name_src = el.name
        else:
            continue

        if selector in seen_selectors:
            continue

        # Links and images contribute a URL field; everything else needs text.
        if el.name == "a" and el.get("href"):
            seen_selectors.add(selector)
            rules.append(ExtractorRule(
                name=_clean_name(name_src if classes else "link", used_names),
                selector=selector, attribute="href"))
        elif el.name == "img" and el.get("src"):
            seen_selectors.add(selector)
            rules.append(ExtractorRule(
                name=_clean_name(name_src if classes else "image", used_names),
                selector=selector, attribute="src"))
        elif text:
            seen_selectors.add(selector)
            rules.append(ExtractorRule(
                name=_clean_name(name_src, used_names),
                selector=selector, transform="strip"))

        if len(rules) >= 10:
            break

    # If nothing distinctive was found, fall back to the item's own text.
    if not rules:
        rules.append(ExtractorRule(name="text", selector=":scope", transform="strip"))
    return rules


def detect_fields(html: str, item_selector: str, base_url: str = "") -> list[ExtractorRule]:
    """Detect field rules inside a caller-supplied container selector."""
    container = BeautifulSoup(html, "lxml").select_one(item_selector)
    return _detect_fields(container, base_url) if container else []


def detect_schema(html: str, base_url: str = "") -> tuple[str | None, list[ExtractorRule]]:
    """Return (item_selector, rules). item_selector is None if no repeating
    structure was confidently found.

    Counts class signatures across the whole document (so items wrapped N-per-row
    are still found) and prefers the container that yields the most distinct
    fields — i.e. the smallest meaningful repeating *record*, not its wrapper."""
    soup = BeautifulSoup(html, "lxml")

    counts: Counter[tuple[str, tuple[str, ...]]] = Counter()
    sample_of: dict[tuple[str, tuple[str, ...]], Tag] = {}
    text_total: Counter[tuple[str, tuple[str, ...]]] = Counter()
    for el in soup.find_all(True):
        if not el.get("class"):
            continue
        sig = _signature(el)
        counts[sig] += 1
        text_total[sig] += len(el.get_text(strip=True))
        sample_of.setdefault(sig, el)

    best_score = 0.0
    best: tuple[str, list[ExtractorRule]] | None = None
    for sig, count in counts.items():
        if count < 3:
            continue
        if text_total[sig] / count < 15:        # skip decorative/empty repeats
            continue
        rules = _detect_fields(sample_of[sig], base_url)
        if not rules:
            continue
        # Favour many repeats AND many distinct fields → the true record level.
        score = count * (len(rules) + 1)
        if score > best_score:
            best_score = score
            best = (_selector_for(*sig), rules)

    if best is None:
        return None, []
    return best
