from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import datetime
from pathlib import Path

import aiofiles
import aiosqlite

from core.models import ExportConfig, ScrapedItem, WebhookConfig


class Exporter:
    def __init__(self, config: ExportConfig, job_name: str):
        self._config = config
        self._job_name = job_name
        self._dir = Path(config.output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._stem = config.filename or f"{job_name}_{ts}"
        self.written: list[str] = []

    async def export_all(self, items: list[ScrapedItem]) -> list[str]:
        if not items:
            return []
        tasks = []
        fmts = self._config.formats
        if "json" in fmts:
            tasks.append(self._export_jsonl(items))
        if "csv" in fmts:
            tasks.append(self._export_csv(items))
        if "sqlite" in fmts:
            tasks.append(self._export_sqlite(items))
        paths = await asyncio.gather(*tasks)
        self.written = [str(p) for p in paths if p]
        return self.written

    async def _export_jsonl(self, items: list[ScrapedItem]) -> Path:
        path = self._dir / f"{self._stem}.jsonl"
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            for item in items:
                row = {
                    "url": item.url,
                    "scraped_at": item.scraped_at.isoformat(),
                    "status_code": item.status_code,
                    "error": item.error,
                    **item.data,
                }
                await f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        return path

    async def _export_csv(self, items: list[ScrapedItem]) -> Path:
        path = self._dir / f"{self._stem}.csv"
        meta = {"url", "scraped_at", "status_code", "error"}
        data_keys: list[str] = []
        seen: set[str] = set()
        for item in items:
            for k in item.data:
                if k not in seen:
                    seen.add(k)
                    data_keys.append(k)
        fieldnames = sorted(meta) + data_keys

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for item in items:
            row: dict = {
                "url": item.url,
                "scraped_at": item.scraped_at.isoformat(),
                "status_code": item.status_code,
                "error": item.error or "",
                **{
                    k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                    for k, v in item.data.items()
                },
            }
            writer.writerow(row)

        async with aiofiles.open(path, "w", encoding="utf-8", newline="") as f:
            await f.write(buf.getvalue())
        return path

    async def _export_sqlite(self, items: list[ScrapedItem]) -> Path:
        db_path = Path(self._config.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS scraped_items (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_name    TEXT,
                    url         TEXT NOT NULL,
                    scraped_at  TEXT,
                    status_code INTEGER,
                    error       TEXT,
                    data        TEXT
                )
            """)
            await db.executemany(
                "INSERT INTO scraped_items "
                "(job_name, url, scraped_at, status_code, error, data) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        self._job_name,
                        item.url,
                        item.scraped_at.isoformat(),
                        item.status_code,
                        item.error,
                        json.dumps(item.data, ensure_ascii=False, default=str),
                    )
                    for item in items
                ],
            )
            await db.commit()
        return db_path
