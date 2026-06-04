import asyncio
import json
from pathlib import Path

from core.models import ExportConfig, ScrapedItem
from pipeline.exporters import Exporter


def _items():
    return [
        ScrapedItem(url="https://e.com/1", data={"name": "A", "n": 1}),
        ScrapedItem(url="https://e.com/2", data={"name": "B", "n": 2}),
    ]


def test_all_formats_written(tmp_path):
    cfg = ExportConfig(
        formats=["csv", "json_pretty", "xlsx", "pdf", "markdown", "html"],
        output_dir=str(tmp_path), filename="t",
    )
    paths = asyncio.run(Exporter(cfg, "t").export_all(_items()))
    exts = {Path(p).suffix for p in paths}
    assert {".csv", ".json", ".xlsx", ".pdf", ".md", ".html"} <= exts
    for p in paths:
        assert Path(p).stat().st_size > 0


def test_json_pretty_content(tmp_path):
    cfg = ExportConfig(formats=["json_pretty"], output_dir=str(tmp_path), filename="t")
    paths = asyncio.run(Exporter(cfg, "t").export_all(_items()))
    data = json.loads(Path(paths[0]).read_text(encoding="utf-8"))
    assert len(data) == 2 and data[0]["name"] == "A"


def test_pdf_is_valid(tmp_path):
    cfg = ExportConfig(formats=["pdf"], output_dir=str(tmp_path), filename="t")
    paths = asyncio.run(Exporter(cfg, "t").export_all(_items()))
    assert Path(paths[0]).read_bytes()[:4] == b"%PDF"


def test_empty_items_writes_nothing(tmp_path):
    cfg = ExportConfig(formats=["csv"], output_dir=str(tmp_path), filename="t")
    assert asyncio.run(Exporter(cfg, "t").export_all([])) == []
