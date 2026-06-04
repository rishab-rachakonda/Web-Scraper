"""Search the scraped Constituent Assembly Debates.

Loads the JSONL produced by cad_extract.py into a SQLite FTS5 index (built
once, cached on disk) and lets you search 38k+ speeches instantly by full
text, speaker, volume, or referenced article.

Examples:
    python cad_query.py "secular state"                 # full-text search
    python cad_query.py "minority rights" --speaker Ambedkar
    python cad_query.py --article 25                    # every speech citing Article 25
    python cad_query.py --speaker "H. V. Kamath" --volume 7
    python cad_query.py "fundamental rights" --limit 20 --full
    python cad_query.py --rebuild                        # force re-index

The index lives at output/cad_speeches/cad_index.db and is rebuilt
automatically when a newer .jsonl appears.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console(legacy_windows=False)

DATA_DIR = Path("output/cad_speeches")
DB_PATH = DATA_DIR / "cad_index.db"


def latest_jsonl() -> Path | None:
    files = sorted(glob.glob(str(DATA_DIR / "*.jsonl")))
    return Path(files[-1]) if files else None


def needs_rebuild(jsonl: Path) -> bool:
    if not DB_PATH.exists():
        return True
    return jsonl.stat().st_mtime > DB_PATH.stat().st_mtime


def build_index(jsonl: Path) -> None:
    console.print(f"[cyan]Indexing[/] {jsonl.name} ...")
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    con.execute(
        """CREATE VIRTUAL TABLE speeches USING fts5(
               volume, date, speaker, text, articles,
               tokenize='porter unicode61'
           )"""
    )
    rows = 0
    with con:
        with jsonl.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                con.execute(
                    "INSERT INTO speeches VALUES (?,?,?,?,?)",
                    (
                        str(r.get("volume") or ""),
                        r.get("date") or "",
                        r.get("speaker") or "",
                        r.get("text") or "",
                        " ".join(r.get("articles_referenced") or []),
                    ),
                )
                rows += 1
    con.close()
    console.print(f"[green]Indexed[/] {rows} speeches -> {DB_PATH}")


def build_query(args) -> tuple[str, list]:
    where: list[str] = []
    params: list = []
    if args.terms:
        where.append("text MATCH ?")
        params.append(args.terms)
    if args.speaker:
        where.append("speaker LIKE ?")
        params.append(f"%{args.speaker}%")
    if args.volume:
        where.append("volume = ?")
        params.append(str(args.volume))
    if args.article:
        # match the article number as a whole token in the articles column
        where.append("articles MATCH ?")
        params.append(f'"{args.article}"')
    clause = " AND ".join(where) if where else "1=1"
    # rank by FTS relevance when there's a text search, else by volume/date order
    order = "ORDER BY rank" if args.terms else "ORDER BY rowid"
    sql = (
        "SELECT volume, date, speaker, text, articles "
        f"FROM speeches WHERE {clause} {order} LIMIT ?"
    )
    params.append(args.limit)
    return sql, params


def render(rows: list[sqlite3.Row], full: bool) -> None:
    if not rows:
        console.print("[yellow]No matching speeches.[/]")
        return
    table = Table(show_lines=full, header_style="bold cyan", expand=False)
    table.add_column("Vol", justify="right", width=3, no_wrap=True)
    table.add_column("Date", width=18, no_wrap=True)
    table.add_column("Speaker", width=20, no_wrap=True, overflow="ellipsis")
    table.add_column("Art.", width=9, no_wrap=True, overflow="ellipsis")
    table.add_column("Text", overflow="ellipsis")
    for vol, date, speaker, text, articles in rows:
        collapsed = " ".join(text.split())
        # In snippet mode pre-truncate so the flexible Text column never
        # starves the fixed columns; full mode shows everything (wrapped).
        snippet = collapsed if full else collapsed[:200]
        table.add_row(vol, date, speaker, articles or "—", snippet)
    console.print(table)
    console.print(f"[dim]{len(rows)} result(s).[/]")


def main() -> None:
    ap = argparse.ArgumentParser(description="Search the Constituent Assembly Debates.")
    ap.add_argument("terms", nargs="?", default="", help="Full-text search terms (FTS5 syntax)")
    ap.add_argument("--speaker", help="Filter by speaker (substring match)")
    ap.add_argument("--volume", type=int, help="Filter by volume number")
    ap.add_argument("--article", help="Filter to speeches referencing this article number")
    ap.add_argument("--limit", type=int, default=10, help="Max results (default 10)")
    ap.add_argument("--full", action="store_true", help="Show full speech text, not a snippet")
    ap.add_argument("--rebuild", action="store_true", help="Force rebuild of the search index")
    args = ap.parse_args()

    jsonl = latest_jsonl()
    if jsonl is None:
        console.print("[red]No CAD data found.[/] Run cad_extract.py first.")
        sys.exit(1)

    if args.rebuild or needs_rebuild(jsonl):
        build_index(jsonl)

    if not (args.terms or args.speaker or args.volume or args.article):
        console.print(Panel.fit(
            "Index ready. Give me something to search:\n"
            '  python cad_query.py "secular state"\n'
            "  python cad_query.py --speaker Ambedkar --article 25",
            title="CAD search", border_style="cyan"))
        return

    con = sqlite3.connect(DB_PATH)
    sql, params = build_query(args)
    try:
        rows = con.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        console.print(f"[red]Query error:[/] {e}")
        console.print('[dim]Tip: quote multi-word phrases, e.g. \'"secular state"\'[/]')
        sys.exit(1)
    finally:
        con.close()
    render(rows, args.full)


if __name__ == "__main__":
    main()
