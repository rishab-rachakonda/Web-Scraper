"""Web UI backend — paste a link, scrape it, download in any format.

A thin FastAPI layer over the smart ScraperEngine:
  POST /api/scrape          {url, item_selector?}  -> detected schema + preview
  GET  /api/download/{id}   ?fmt=csv               -> the data as a file

Run with:  python scraper.py serve   (or: uvicorn web.app:app)
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.engine import ScraperEngine
from core.models import ExportConfig, JobConfig, ScrapedItem
from pipeline.exporters import Exporter

app = FastAPI(title="Greatest Web Scraper")

_STATIC = Path(__file__).parent / "static"
_DOWNLOAD_DIR = Path("output") / "web"
_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# In-memory results, keyed by job id (cleared on restart).
_RESULTS: dict[str, list[ScrapedItem]] = {}

_FORMATS = {
    "csv":  ("csv", "text/csv"),
    "xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "json": ("json_pretty", "application/json"),
    "jsonl": ("jsonl", "application/x-ndjson"),
    "pdf":  ("pdf", "application/pdf"),
    "markdown": ("markdown", "text/markdown"),
    "html": ("html", "text/html"),
}


class ScrapeRequest(BaseModel):
    url: str
    item_selector: str | None = None


def _fields(items: list[ScrapedItem]) -> list[str]:
    seen: list[str] = []
    for it in items:
        for k in it.data:
            if not k.startswith("_") and k not in seen:
                seen.append(k)
    return seen


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (_STATIC / "index.html").read_text(encoding="utf-8")


@app.post("/api/scrape")
async def scrape(req: ScrapeRequest):
    url = req.url.strip()
    if not url:
        raise HTTPException(400, "Please provide a URL.")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    job = JobConfig(
        name="web",
        urls=[url],
        mode="smart",
        item_selector=req.item_selector or None,
        export=ExportConfig(formats=[], output_dir=str(_DOWNLOAD_DIR)),
    )
    engine = ScraperEngine(job)
    try:
        items = await engine.run()
    except Exception as exc:
        raise HTTPException(500, f"Scrape failed: {exc}")

    items = [it for it in items if not it.error] or items
    if not items:
        raise HTTPException(422, "Nothing could be extracted from that page.")

    job_id = uuid.uuid4().hex[:12]
    _RESULTS[job_id] = items
    fields = _fields(items)
    preview = [{k: it.data.get(k) for k in fields} for it in items[:100]]
    return {
        "job_id": job_id,
        "item_selector": job.item_selector,
        "fields": fields,
        "count": len(items),
        "notes": engine.smart_notes,
        "preview": preview,
        "preview_truncated": len(items) > 100,
    }


@app.get("/api/download/{job_id}")
async def download(job_id: str, fmt: str = "csv"):
    items = _RESULTS.get(job_id)
    if items is None:
        raise HTTPException(404, "Result expired — please scrape again.")
    if fmt not in _FORMATS:
        raise HTTPException(400, f"Unknown format '{fmt}'.")

    export_fmt, media = _FORMATS[fmt]
    cfg = ExportConfig(formats=[export_fmt], output_dir=str(_DOWNLOAD_DIR), filename=f"scrape_{job_id}")
    paths = await Exporter(cfg, "scrape").export_all(items)
    if not paths:
        raise HTTPException(500, "Export produced no file.")
    path = Path(paths[0])
    return FileResponse(path, media_type=media, filename=path.name)


app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
