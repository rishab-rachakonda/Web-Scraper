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


def _candidates(sample: Tag) -> list[tuple[Tag, str, str, bool]]:
    """Elements worth turning into fields: (el, selector, name_src, has_class)."""
    out: list[tuple[Tag, str, str, bool]] = []
    for el in sample.find_all(True):
        if el is sample:
            continue
        classes = [c for c in (el.get("class") or []) if c not in _NOISE_CLASSES]
        if classes:
            out.append((el, "." + classes[0], classes[0], True))
        elif el.name in _FIELDISH_TAGS:
            out.append((el, el.name, el.name, False))
    return out


def _is_meaningful(el: Tag) -> bool:
    """A candidate that actually carries data — real text, a link, or an image.
    Decorative empties (icon <i>, spacer <span>) don't count."""
    if el.name == "a" and el.get("href"):
        return True
    if el.name == "img" and el.get("src"):
        return True
    return bool(el.get_text(strip=True))


def _detect_fields(sample: Tag) -> list[ExtractorRule]:
    candidates = _candidates(sample)
    meaningful_ids = {id(el) for el, *_ in candidates if _is_meaningful(el)}

    def is_wrapper(el: Tag) -> bool:
        # A field whose descendants include a *meaningful* candidate is a
        # container (e.g. country_info), not a leaf — drop it. Decorative
        # children (a flag icon with no text) don't make it a wrapper.
        return any(id(d) in meaningful_ids for d in el.find_all(True))

    rules: list[ExtractorRule] = []
    used_names: set[str] = set()
    seen_selectors: set[str] = set()
    seen_values: set[str] = set()

    for el, selector, name_src, has_class in candidates:
        if selector in seen_selectors or len(rules) >= 10:
            continue
        rule = _rule_for(el, selector, name_src, has_class, is_wrapper, used_names, seen_values)
        if rule is not None:
            seen_selectors.add(selector)
            rules.append(rule)

    if not rules:                       # nothing distinctive → the item's own text
        rules.append(ExtractorRule(name="text", selector=":scope", transform="strip"))
    return rules


def _rule_for(el, selector, name_src, has_class, is_wrapper, used_names, seen_values):
    if el.name == "a" and el.get("href"):
        return ExtractorRule(name=_clean_name(name_src if has_class else "link", used_names),
                             selector=selector, attribute="href")
    if el.name == "img" and el.get("src"):
        return ExtractorRule(name=_clean_name(name_src if has_class else "image", used_names),
                             selector=selector, attribute="src")
    if is_wrapper(el):                  # drop containers like country_info
        return None
    text = el.get_text(" ", strip=True)
    if not text or text in seen_values:  # drop empties and duplicate values
        return None
    seen_values.add(text)
    # 'auto' casts numbers/currency/dates; falls back to clean text.
    return ExtractorRule(name=_clean_name(name_src, used_names), selector=selector, transform="auto")


def detect_fields(html: str, item_selector: str) -> list[ExtractorRule]:
    """Detect field rules inside a caller-supplied container selector."""
    container = BeautifulSoup(html, "lxml").select_one(item_selector)
    return _detect_fields(container) if container else []


def detect_schema(html: str) -> tuple[str | None, list[ExtractorRule]]:
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
        rules = _detect_fields(sample_of[sig])
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
