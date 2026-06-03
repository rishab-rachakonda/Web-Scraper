"""Constituent Assembly Debates -> structured speech records.

Scrapes constitutionofindia.net debates and emits one JSON record per
*speaker turn* in this shape:

    {
      "volume": 7,
      "date": "6th December 1948",
      "speaker": "B.R. Ambedkar",
      "text": "The article as drafted...",
      "articles_referenced": ["25", "26"]
    }

The site lays each numbered speech out as a 12-column grid row:
    col-span-3  -> a "<vol>.<section>.<para>" id badge + the speaker name
    col-span-9  -> the speech paragraph
Continuation paragraphs of the same speaker have an empty speaker cell, so we
forward-fill the last speaker and merge consecutive paragraphs into one turn.

Usage:
    python cad_extract.py --volumes 1
    python cad_extract.py --volumes 1 7 9
    python cad_extract.py --all
    python cad_extract.py --url https://www.constitutionofindia.net/debates/06-dec-1948/
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

console = Console(legacy_windows=False)

BASE = "https://www.constitutionofindia.net"
VOLUME_URL = BASE + "/constituent-assembly-debate/volume-{n}/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}
MAX_VOLUMES = 12  # CAD has 12 volumes

# "article 19", "articles 25 and 26", "articles 19 to 22", "article 25, 26 & 27"
_ARTICLE_RE = re.compile(
    r"\barticles?\s+((?:\d+)(?:\s*(?:,|and|to|&|–|—|-)\s*\d+)*)",
    re.IGNORECASE,
)
_NUM_RE = re.compile(r"\d+")


def reformat_date(h1_text: str) -> str:
    """'06 Dec 1948' -> '6th December 1948'."""
    raw = h1_text.strip()
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            dt = datetime.strptime(raw, fmt)
            break
        except ValueError:
            dt = None
    if dt is None:
        return raw  # leave whatever the page had
    day = dt.day
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix} {dt.strftime('%B %Y')}"


def extract_articles(text: str) -> list[str]:
    """Pull article numbers referenced in a speech, in order, de-duplicated."""
    found: list[str] = []
    for group in _ARTICLE_RE.findall(text):
        for num in _NUM_RE.findall(group):
            if num not in found:
                found.append(num)
    return found


def parse_debate(html: str) -> list[dict]:
    """Turn one debate page into a list of speaker-turn records."""
    soup = BeautifulSoup(html, "lxml")
    main = soup.select_one("main") or soup
    h1 = main.select_one("h1")
    date = reformat_date(h1.get_text(strip=True)) if h1 else ""

    turns: list[dict] = []
    last_speaker = ""
    volume: int | None = None

    for row in main.select(r"div.lg\:grid[id]"):
        right = row.select_one(r".lg\:col-span-9")
        left = row.select_one(r".lg\:col-span-3")
        if not right or not left:
            continue
        badge = left.select_one(r"span.bg-\[\#F8FFA3\]")
        pid = badge.get_text(strip=True) if badge else ""
        if not pid:
            continue  # skip preamble / section headers (no numbered id)

        if volume is None and "." in pid:
            try:
                volume = int(pid.split(".")[0])
            except ValueError:
                pass

        speaker = ""
        for sp in left.find_all("span"):
            t = sp.get_text(" ", strip=True)
            if t and t != pid:
                speaker = t
                break
        if speaker:
            last_speaker = speaker

        text = right.get_text(" ", strip=True)
        if not text:
            continue

        # Merge continuation paragraphs into the current speaker's turn.
        if speaker or not turns:
            turns.append({"speaker": last_speaker, "_parts": [text]})
        else:
            turns[-1]["_parts"].append(text)

    records: list[dict] = []
    for t in turns:
        full = " ".join(t["_parts"]).strip()
        records.append(
            {
                "volume": volume,
                "date": date,
                "speaker": t["speaker"],
                "text": full,
                "articles_referenced": extract_articles(full),
            }
        )
    return records


async def fetch(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url, follow_redirects=True, timeout=40)
    resp.raise_for_status()
    return resp.content.decode("utf-8", "replace")


async def discover_debate_urls(client: httpx.AsyncClient, volume: int) -> list[str]:
    html = await fetch(client, VOLUME_URL.format(n=volume))
    soup = BeautifulSoup(html, "lxml")
    urls: list[str] = []
    for a in soup.select('a[href*="/debates/"]'):
        href = a.get("href") or ""
        full = href if href.startswith("http") else BASE + href
        if full not in urls:
            urls.append(full)
    return urls


async def run(volumes: list[int], single_url: str | None, out_dir: Path) -> None:
    sem = asyncio.Semaphore(5)
    all_records: list[dict] = []

    async with httpx.AsyncClient(headers=HEADERS, http2=True) as client:
        # 1. Build the list of debate pages.
        if single_url:
            debate_urls = [single_url]
            console.print(f"[cyan]Target:[/] {single_url}")
        else:
            debate_urls = []
            for v in volumes:
                try:
                    found = await discover_debate_urls(client, v)
                    console.print(f"[cyan]Volume {v}:[/] {len(found)} debates")
                    debate_urls.extend(found)
                except httpx.HTTPError as exc:
                    console.print(f"[red]Volume {v} failed:[/] {exc}")

        if not debate_urls:
            console.print("[red]No debate pages found.[/]")
            return

        # 2. Scrape + parse each debate page concurrently.
        async def worker(url: str, progress, task) -> list[dict]:
            async with sem:
                try:
                    html = await fetch(client, url)
                    recs = parse_debate(html)
                except httpx.HTTPError as exc:
                    console.print(f"[red]Failed {url}:[/] {exc}")
                    recs = []
                await asyncio.sleep(0.2)  # be polite
                progress.advance(task)
                return recs

        with Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Scraping debates", total=len(debate_urls))
            results = await asyncio.gather(
                *(worker(u, progress, task) for u in debate_urls)
            )
        for recs in results:
            all_records.extend(recs)

    # 3. Write output.
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path = out_dir / f"cad_speeches_{stamp}.jsonl"
    json_path = out_dir / f"cad_speeches_{stamp}.json"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for rec in all_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)

    console.print()
    console.print(f"[bold green]Done.[/] {len(all_records)} speech records "
                  f"from {len(debate_urls)} debate(s).")
    console.print(f"  -> {jsonl_path}")
    console.print(f"  -> {json_path}")
    if all_records:
        console.print("\n[bold]Sample record:[/]")
        console.print_json(json.dumps(all_records[min(6, len(all_records) - 1)],
                                      ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrape Constituent Assembly Debates into structured speech records.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--volumes", type=int, nargs="+", help="Volume numbers, e.g. --volumes 1 7")
    g.add_argument("--all", action="store_true", help=f"All {MAX_VOLUMES} volumes")
    g.add_argument("--url", type=str, help="A single debate page URL")
    ap.add_argument("-o", "--out", type=Path, default=Path("output/cad_speeches"),
                    help="Output directory (default: output/cad_speeches)")
    args = ap.parse_args()

    volumes = list(range(1, MAX_VOLUMES + 1)) if args.all else (args.volumes or [])
    asyncio.run(run(volumes, args.url, args.out))


if __name__ == "__main__":
    main()
