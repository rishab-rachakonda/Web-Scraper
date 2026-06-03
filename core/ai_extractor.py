"""
Schema-based extractor — no AI required.

An output schema YAML file defines field names, selectors (CSS / XPath /
JSONPath / regex), types, and transforms.  SchemaExtractor loads it and
delegates to the existing DataExtractor, so output always matches your
declared shape.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .extractor import DataExtractor
from .models import ExtractorRule


class OutputSchema:
    """
    Loaded from a YAML file.  Each field entry maps directly to an
    ExtractorRule, giving you a reusable, job-independent format definition.
    """

    def __init__(self, schema_path: str):
        with open(schema_path, encoding="utf-8") as f:
            raw: dict = yaml.safe_load(f)

        self.name: str = raw["name"]
        self.description: str = raw.get("description", "")
        self.fields: list[dict] = raw.get("fields", [])

    def to_rules(self) -> list[ExtractorRule]:
        rules = []
        for field in self.fields:
            rules.append(ExtractorRule(
                name=field["name"],
                selector=field.get("selector"),
                xpath=field.get("xpath"),
                jsonpath=field.get("jsonpath"),
                regex=field.get("regex"),
                attribute=field.get("attribute"),
                multiple=field.get("multiple", False),
                transform=field.get("transform"),
            ))
        return rules


class SchemaExtractor:
    """Wraps DataExtractor with rules loaded from an OutputSchema."""

    def __init__(self, schema: OutputSchema):
        self._extractor = DataExtractor(schema.to_rules())

    def extract_html(self, html: str, url: str = "") -> dict[str, Any]:
        return self._extractor.extract_html(html, url)

    def extract_json(self, data: Any) -> dict[str, Any]:
        return self._extractor.extract_json(data)
