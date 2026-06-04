"""Cross-run state: change detection + resume/checkpoint.

One JSON file per job under output/.state/ holds the content hash of every URL
seen in previous runs (so we can label items new/changed/unchanged) plus the set
of URLs completed in an interrupted run. When resume is on, items are also
streamed to a sidecar .items.jsonl so an interrupted crawl loses nothing.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import ScrapedItem


def content_hash(data: dict) -> str:
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class RunStore:
    def __init__(self, job_name: str, base_dir: str = "output"):
        state_dir = Path(base_dir) / ".state"
        state_dir.mkdir(parents=True, exist_ok=True)
        self._path = state_dir / f"{job_name}.json"
        self._items_path = state_dir / f"{job_name}.items.jsonl"

        # Identity is the record's content hash, not its URL: with item_selector
        # many records share one URL, so a per-URL key can't tell them apart.
        self._prev: set[str] = set()         # content hashes seen in the last run
        self.completed: set[str] = set()     # urls finished in an interrupted run
        if self._path.exists():
            try:
                d = json.loads(self._path.read_text(encoding="utf-8"))
                self._prev = set(d.get("hashes", []))
                self.completed = set(d.get("completed", []))
            except Exception:
                pass

        self._current: set[str] = set()      # content hashes seen this run
        self.new = self.unchanged = 0
        self._items_fh = None

    # ── change detection ────────────────────────────────────────────────────────

    def classify(self, item: ScrapedItem) -> str:
        """A record is 'unchanged' if an identical record existed last run,
        otherwise 'new' (covers brand-new and content-changed records)."""
        h = content_hash(item.data)
        self._current.add(h)
        if h in self._prev:
            self.unchanged += 1
            return "unchanged"
        self.new += 1
        return "new"

    # ── resume / checkpoint ─────────────────────────────────────────────────────

    def load_resume_items(self) -> list[ScrapedItem]:
        """Items already collected in a previous interrupted run."""
        if not self._items_path.exists():
            return []
        items: list[ScrapedItem] = []
        for line in self._items_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                items.append(ScrapedItem(url=rec.pop("url", ""),
                                         status_code=rec.pop("status_code", None),
                                         error=rec.pop("error", None),
                                         data={k: v for k, v in rec.items()
                                               if k not in ("scraped_at", "_change")}))
            except Exception:
                continue
        return items

    def checkpoint(self, item: ScrapedItem):
        """Append a completed item to the resume sidecar (called as we go)."""
        if self._items_fh is None:
            self._items_fh = self._items_path.open("a", encoding="utf-8")
        row = {"url": item.url, "status_code": item.status_code,
               "error": item.error, **item.data}
        self._items_fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        self._items_fh.flush()
        self.completed.add(item.url)

    # ── persistence ─────────────────────────────────────────────────────────────

    def save(self, finished: bool = True):
        if self._items_fh is not None:
            self._items_fh.close()
            self._items_fh = None
        self._path.write_text(json.dumps(
            {"hashes": sorted(self._current), "completed": [] if finished else sorted(self.completed)},
            ensure_ascii=False), encoding="utf-8")
        if finished and self._items_path.exists():
            self._items_path.unlink()   # crawl completed cleanly → drop the sidecar
