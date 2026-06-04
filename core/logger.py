"""Structured per-run logging.

Writes greppable key=value log lines to output/<dir>/.logs/<job>.log. File-only
by default so it never garbles the live Rich dashboard.
"""
from __future__ import annotations

import logging
from pathlib import Path


def get_run_logger(job_name: str, output_dir: str, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(f"scraper.{job_name}")
    logger.setLevel(level.upper())
    logger.handlers.clear()
    logger.propagate = False

    log_dir = Path(output_dir) / ".logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_dir / f"{job_name}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    logger.addHandler(handler)
    return logger


def kv(**fields) -> str:
    """Render fields as a structured `key=value` line."""
    parts = []
    for k, v in fields.items():
        s = str(v)
        if " " in s:
            s = f'"{s}"'
        parts.append(f"{k}={s}")
    return " ".join(parts)
