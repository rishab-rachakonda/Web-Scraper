"""
Greatest Web Scraper — async, multi-engine, beautiful.

Usage:
    python scraper.py run example_jobs/quotes.yaml
    python scraper.py quick https://example.com --select "h1"
    python scraper.py schedule example_jobs/quotes.yaml
    python scraper.py validate example_jobs/hackernews.yaml
    python scraper.py info
"""
from __future__ import annotations

import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

import asyncio
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich import box
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(
    name="scraper",
    help="[bold bright_cyan]The Greatest Web Scraper on the Planet[/]",
    add_completion=False,
    rich_markup_mode="rich",
    no_args_is_help=True,
)
console = Console(legacy_windows=False)

_JOB_FILE_HELP = "Path to job YAML file"


def _load_job(path: Path):
    from core.models import JobConfig
    with open(path, encoding="utf-8") as f:
        return JobConfig(**yaml.safe_load(f))


def _collect_preview_keys(items) -> list[str]:
    seen: set[str] = set()
    keys: list[str] = []
    for item in items:
        for k in item.data:
            if not k.startswith("_") and k not in seen:
                seen.add(k)
                keys.append(k)
    return keys[:6]


def _build_preview_row(item, keys: list[str]) -> list[str]:
    url = item.url[:40] + ("..." if len(item.url) > 40 else "")
    cells = [url]
    for k in keys:
        v = item.data.get(k)
        cells.append(str(v)[:55] if v is not None else "")
    return cells


def _print_preview(items):
    if not items:
        return
    keys = _collect_preview_keys(items)
    if not keys:
        return

    t = Table(
        title="[bold bright_cyan] EXTRACTED DATA (preview - first 10 rows) [/]",
        box=box.ROUNDED,
        border_style="cyan",
        header_style="bold bright_cyan",
        show_lines=True,
        expand=True,
    )
    t.add_column("URL", style="dim cyan", max_width=40, no_wrap=True)
    for k in keys:
        t.add_column(k.replace("_", " ").title(), style="bright_white", max_width=55)

    for item in items:
        if not item.error:
            t.add_row(*_build_preview_row(item, keys))

    console.print()
    console.print(t)


async def _run_job(job, verbose: bool = False):
    from core.engine import ScraperEngine
    from core.models import ScraperStats, ScrapedItem
    from pipeline.exporters import Exporter
    from ui.dashboard import ScraperDashboard

    dash = ScraperDashboard(job.name)
    items: list[ScrapedItem] = []

    dash.start(total=len(job.urls))

    def on_item(item: ScrapedItem):
        items.append(item)
        if verbose and item.error:
            console.log(f"[red]ERR[/] {item.url}: {item.error}")

    def on_stats(stats: ScraperStats):
        if items:
            dash.update(items[-1], stats)

    engine = ScraperEngine(job, on_item=on_item, on_stats=on_stats)

    try:
        all_items = await engine.run()
        dash.stop()
        if engine.smart_notes:
            from rich.panel import Panel
            console.print(Panel(
                "\n".join(f"• {n}" for n in engine.smart_notes),
                title="🧠 smart decisions", border_style="bright_magenta", expand=False))
        paths = await Exporter(job.export, job.name).export_all(all_items)
        dash.print_summary(paths)
        if "terminal" in job.export.formats:
            _print_preview(all_items[:10])
    except KeyboardInterrupt:
        dash.stop()
        console.print("\n[yellow]Interrupted.[/]")
        raise typer.Exit(0)
    except Exception as exc:
        dash.stop()
        console.print(f"\n[red bold]Error:[/] {exc}")
        if verbose:
            console.print_exception()
        raise typer.Exit(1)


@app.command()
def run(
    job_file: Path = typer.Argument(..., help=_JOB_FILE_HELP, exists=True),
    output_dir: Optional[str] = typer.Option(None, "--output", "-o", help="Override output directory"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Force HTTP mode (skip Playwright)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed error logs"),
):
    """[bold bright_cyan]Run a scraping job defined in a YAML file.[/]"""
    job = _load_job(job_file)
    if output_dir:
        job.export.output_dir = output_dir
    if no_browser:
        job.mode = "http"
    asyncio.run(_run_job(job, verbose))


@app.command()
def quick(
    url: str = typer.Argument(..., help="URL to scrape"),
    selector: Optional[str] = typer.Option(None, "--select", "-s", help="CSS selector to extract"),
    attribute: Optional[str] = typer.Option(None, "--attr", "-a", help="HTML attribute to pull (e.g. href)"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output file (.json/.csv)"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow same-domain links"),
    depth: int = typer.Option(1, "--depth", "-d", help="Max crawl depth when following links"),
    use_browser: bool = typer.Option(False, "--browser", "-b", help="Use Playwright browser engine"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """[bold bright_cyan]Instantly scrape a URL — no config file needed.[/]"""
    from core.models import ExportConfig, ExtractorRule, JobConfig

    rules = []
    if selector:
        rules.append(ExtractorRule(
            name="data",
            selector=selector,
            attribute=attribute or None,
            multiple=True,
        ))

    formats = ["terminal"]
    out_dir = "output"
    stem = None
    if output:
        p = Path(output)
        stem = p.stem
        out_dir = str(p.parent)
        ext = p.suffix.lower().lstrip(".")
        formats = ["json", "terminal"] if ext in ("json", "jsonl") else ["csv", "terminal"]

    job = JobConfig(
        name=stem or "quick",
        urls=[url],
        mode="browser" if use_browser else "auto",
        rules=rules,
        follow_links=follow,
        max_depth=depth,
        export=ExportConfig(formats=formats, output_dir=out_dir, filename=stem),
    )
    asyncio.run(_run_job(job, verbose))


@app.command()
def smart(
    url: str = typer.Argument(..., help="URL to scrape"),
    item: Optional[str] = typer.Option(None, "--item", help="Override the auto-detected item_selector"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output file (.json/.csv) or dir"),
    pages: int = typer.Option(1, "--pages", "-p", help="Max pages to auto-paginate"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """[bold bright_magenta]Let the scraper decide everything[/] — transport, browser-vs-HTTP,
    and the extraction schema. Give it just a URL; override only what you want."""
    from core.models import ExportConfig, JobConfig, PaginationConfig

    out_dir, stem, formats = "output/smart", None, ["json", "csv", "terminal"]
    if output:
        p = Path(output)
        if p.suffix:
            stem, out_dir = p.stem, str(p.parent) or "output/smart"
            formats = ["json", "terminal"] if p.suffix.lower().lstrip(".") in ("json", "jsonl") else ["csv", "terminal"]
        else:
            out_dir = str(p)

    job = JobConfig(
        name=stem or "smart",
        urls=[url],
        mode="smart",                       # the project decides the rest
        item_selector=item,                 # None unless you override
        pagination=PaginationConfig(auto=True, max_pages=pages) if pages > 1 else None,
        export=ExportConfig(formats=formats, output_dir=out_dir, filename=stem),
    )
    asyncio.run(_run_job(job, verbose))


@app.command(name="schedule")
def schedule_cmd(
    job_file: Path = typer.Argument(..., help=_JOB_FILE_HELP, exists=True),
):
    """[bold bright_cyan]Run a job repeatedly on its cron schedule.[/]"""
    job = _load_job(job_file)

    if not job.schedule:
        console.print("[red]Error:[/] No 'schedule' cron expression in this job file.")
        raise typer.Exit(1)

    from scheduler.runner import JobScheduler

    sched = JobScheduler()

    async def _run():
        await _run_job(job)

    sched.add(job, _run)
    sched.start()

    console.print(Panel(
        f"[bright_cyan]Job:[/]      {job.name}\n"
        f"[bright_cyan]Schedule:[/] {job.schedule}\n\n"
        "[dim]Press Ctrl+C to stop[/]",
        title="[bold bright_cyan] SCHEDULER RUNNING [/]",
        border_style="cyan",
        padding=(1, 2),
    ))

    try:
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        sched.stop()
        console.print("\n[yellow]Scheduler stopped.[/]")


@app.command()
def validate(
    job_file: Path = typer.Argument(..., help=_JOB_FILE_HELP),
):
    """[bold bright_cyan]Validate a job configuration file.[/]"""
    try:
        job = _load_job(job_file)
        extraction = (
            f"[magenta]schema[/] -> {job.output_schema}"
            if job.output_schema
            else f"inline rules ({len(job.rules)})"
        )
        console.print(Panel(
            f"[green]✓  Valid configuration[/]\n\n"
            f"[bright_cyan]Name        [/] {job.name}\n"
            f"[bright_cyan]Description [/] {job.description or '-'}\n"
            f"[bright_cyan]URLs        [/] {len(job.urls)}\n"
            f"[bright_cyan]Mode        [/] {job.mode}\n"
            f"[bright_cyan]Extraction  [/] {extraction}\n"
            f"[bright_cyan]Export      [/] {', '.join(job.export.formats)}\n"
            f"[bright_cyan]Follow links[/] {job.follow_links} (depth={job.max_depth})\n"
            f"[bright_cyan]Schedule    [/] {job.schedule or 'none'}\n"
            f"[bright_cyan]Proxy       [/] {len(job.proxy.urls) if job.proxy else 0} proxies",
            title="[bold bright_green] JOB VALID [/]",
            border_style="green",
            padding=(1, 2),
        ))
    except Exception as exc:
        console.print(Panel(
            f"[red]✗  {exc}[/]",
            title="[bold red] VALIDATION FAILED [/]",
            border_style="red",
            padding=(1, 2),
        ))
        raise typer.Exit(1)


@app.command()
def info():
    """[bold bright_cyan]Show version and feature overview.[/]"""
    t = Table(box=box.ROUNDED, border_style="bright_cyan", show_header=False)
    t.add_column(style="bright_cyan bold", width=22)
    t.add_column(style="white")

    rows = [
        ("Engines",       "HTTP/2 (httpx)  +  Browser (Playwright Chromium)"),
        ("Auto-detect",   "Detects JS-heavy SPAs and switches engine automatically"),
        ("Auth",          "Basic  |  Bearer token  |  Cookies  |  Form login"),
        ("Proxy",         "Proxy rotation with per-domain rate limiting"),
        ("Rate limiting", "Token-bucket algorithm, configurable burst"),
        ("Retry",         "Exponential backoff, configurable status codes"),
        ("Extraction",    "CSS selectors  |  XPath  |  JMESPath  |  Regex"),
        ("Output schema", "Reusable YAML schema files define your output shape"),
        ("Export",        "JSON Lines  |  CSV  |  SQLite  |  Terminal table"),
        ("Scheduling",    "APScheduler cron integration"),
        ("Link following","Same-domain crawler with configurable depth"),
        ("Dashboard",     "Live Rich TUI with real-time stats and results"),
    ]
    for k, v in rows:
        t.add_row(k, v)

    console.print()
    console.print(Panel(
        Align.center(t),
        title="[bold bright_cyan] GREATEST WEB SCRAPER - Feature Overview [/]",
        border_style="bright_cyan",
        padding=(1, 2),
    ))
    console.print()
    console.print("  [dim]python scraper.py --help[/]  for commands")
    console.print("  [dim]python scraper.py run example_jobs/quotes.yaml[/]  to start scraping")
    console.print()


if __name__ == "__main__":
    app()
